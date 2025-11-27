import asyncio
import base64
import json
import re
import httpx
import pandas as pd
import io
import sys
import os
import subprocess
import tempfile
from bs4 import BeautifulSoup, Comment
from loguru import logger
from app.config import settings
from app.utils.llm_client import query_llm

# Selenium imports
from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.chrome.service import Service


def get_driver():
    """
    Initializes a headless Chrome driver using system Chromium.
    """
    chrome_options = Options()
    chrome_options.add_argument("--headless")
    chrome_options.add_argument("--no-sandbox")
    chrome_options.add_argument("--disable-dev-shm-usage")
    chrome_options.add_argument("--disable-gpu")
    chrome_options.add_argument("--window-size=1920,1080")

    # Explicitly set binary location for Chromium
    chrome_options.binary_location = "/usr/bin/chromium"

    try:
        # Use system chromedriver
        service = Service("/usr/bin/chromedriver")
        driver = webdriver.Chrome(service=service, options=chrome_options)
                            has_submitted_successfully = True
                        else:
                            last_observation = (
                                f"Submission failed: {type(e).__name__}: {str(e)}"
                            )

                    # If we know submission was successful and there is no explicit next level,
                    # rely on has_submitted_successfully flag to stop further decisions.

            elif action == "done":
                logger.info("Agent decided the task is complete.")
                break

            else:
                logger.warning(f"Unknown action: {action}")
                last_observation = f"Error: Unknown action '{action}'"

    except Exception as e:
        logger.error(f"Fatal error in solver loop: {e}")
    finally:
        if driver:
            driver.quit()
        # Cleanup scratchpad
        if 'scratchpad_path' in locals() and os.path.exists(scratchpad_path):
            try:
                os.remove(scratchpad_path)
            except Exception as e:
                logger.warning(f"Failed to remove scratchpad: {e}")


def clean_html(html: str) -> str:
    """
    Cleans HTML to reduce token count while preserving relevant content.
    Removes styles, SVGs, and unnecessary attributes.
    """
    soup = BeautifulSoup(html, "html.parser")

    # Remove irrelevant tags
    for tag in soup(
        [
            "style",
            "svg",
            "path",
            "link",
            "meta",
            "noscript",
            "iframe",
            "footer",
            "header",
        ]
    ):
        tag.decompose()

    # Comments are PRESERVED as they often contain hidden clues for the agent
    # for element in soup(text=lambda text: isinstance(text, Comment)):
    #     element.extract()

    # Clean attributes
    for tag in soup.find_all(True):
        # Keep only essential attributes
        allowed_attrs = [
            "id",
            "name",
            "class",
            "href",
            "src",
            "action",
            "method",
            "type",
            "value",
            "placeholder",
        ]
        attrs = dict(tag.attrs)
        for attr in attrs:
            if attr not in allowed_attrs:
                del tag.attrs[attr]

        # Truncate long class names or src (optional, but good for safety)
        if "src" in tag.attrs and len(tag["src"]) > 500:
            tag["src"] = tag["src"][:500] + "..."

    return str(soup)


async def get_agent_decision(
    html: str,
    url: str,
    last_observation: str,
    email: str,
    secret: str,
    input_file_path: str,
) -> dict:
    """
    Asks the LLM what to do next based on the current page and history.
    """
    # Clean HTML to save tokens
    cleaned_html = clean_html(html)

    # Truncate if still too long (safety net)
    if len(cleaned_html) > 50000:
        cleaned_html = cleaned_html[:50000] + "...(truncated)"

    prompt = f"""
    You are an autonomous agent solving a technical quiz.
    
    Context:
    - Current URL: {url}
    - User Email: {email}
    - User Secret: {secret}
    - Last Observation/Result: {last_observation}
    
    Task:
    Analyze the HTML content below and decide the next step.
    The goal is to find the question, solve it (potentially using Python code), and submit the answer.
    
    Capabilities:
    1. "navigate": Go to a specific URL.
    2. "execute_code": Run Python code. The code has access to the internet and environment variables. Use this to download files, process data, or even submit answers if complex requests are needed.
    3. "submit": Submit a JSON payload to a URL. This is the PREFERRED way to submit the final answer.
    4. "done": Stop the agent.
    
    IMPORTANT INSTRUCTIONS FOR LARGE FILES:
    - If the question involves analyzing a file (CSV, JSON, etc.), DO NOT assume you know its content.
    - Use "execute_code" to download the file and inspect it first (e.g., `print(df.head())`, `print(df.info())`).
    - Only after understanding the data structure should you write the full solution code.
    
    CRITICAL INSTRUCTIONS - READ CAREFULLY:
    1. **NO HARDCODING LARGE DATA**: Never copy-paste large strings (like Base64 data, JSON dumps, or long text) from the HTML into your Python code. It causes SyntaxErrors and crashes.
    2. **ALWAYS READ FROM FILE**: The current page's HTML is saved to `{input_file_path}`. You MUST write Python code to read this file and extract the data programmatically (e.g., using regex or BeautifulSoup).
    3. **SUBMIT IMMEDIATELY**: Check the "Last Observation/Result" above. If it contains the calculated answer (e.g., a number like 495759), DO NOT calculate it again. Use the "submit" action IMMEDIATELY.
    4. **DO NOT PRINT AGAIN**: If you have the answer, do not use "execute_code" to print it. Use "submit".
    
    Example of reading the file correctly:
    ```python
    import re
    import os
    
    # Read the HTML file
    with open(r"{input_file_path}", "r", encoding="utf-8") as f:
        html = f.read()
        
    # ROBUST REGEX PATTERNS (Try these):
    # 1. For Base64 in comments: r"Data Dump:\s*([A-Za-z0-9+/=]+)"
    # 2. For Base64 across lines: r"Data Dump:\s*([A-Za-z0-9+/=\s]+)" (then remove newlines)
    # 3. Use re.DOTALL if content spans lines: re.search(r"pattern", html, re.DOTALL)
    ```

    STOP LOOPING INSTRUCTIONS:
    - If you have already calculated the answer in a previous step, DO NOT calculate it again.
    - If you already know the answer or see it mentioned in the page or in "Last Observation/Result", you MUST choose "submit" as your next and final action.
    - When you know the answer, you MUST NOT choose "execute_code" or "navigate" again.
    - If submission fails with a non-JSON response (e.g. 200 OK text), it is likely a SUCCESS. Do not retry endlessly.

    DECISION PRIORITY (ALWAYS FOLLOW THIS ORDER):
    1) If you have the final answer -> use "submit".
    2) If you do NOT yet have the answer but see data to download/process -> use "execute_code".
    3) If you are clearly on a page that only tells you to go somewhere else -> use "navigate".
    4) Use "done" ONLY when the entire quiz is finished and there is nothing left to submit or navigate to.

    IMPORTANT INSTRUCTIONS FOR MISSING LIBRARIES:
    - If you need a library that might not be installed (e.g., `faker`, `scipy`), you MUST install it within your code.
    - Use this pattern at the top of your code:
      ```python
      import subprocess
      import sys
      def install(package):
          subprocess.check_call([sys.executable, "-m", "pip", "install", package])
      
      try:
          import some_library
      except ImportError:
          install("some_library")
          import some_library
    ```

    HTML Content (Cleaned):
    ```html
    {cleaned_html}
    ```


def execute_code(code: str):
    """
    Executes the given Python code using subprocess for better isolation.
    Writes code to a temp file and runs it.
    Passes current environment variables (including API keys) to the subprocess.
    """
    # Create a temporary file
    with tempfile.NamedTemporaryFile(
        mode="w", suffix=".py", delete=False, encoding="utf-8"
    ) as f:
        f.write(code)
        temp_name = f.name

    try:
        # Run the code
        # We use sys.executable to ensure we use the same python interpreter (with installed packages)
        # We explicitly pass os.environ so the subprocess has access to API keys
        result = subprocess.run(
            [sys.executable, temp_name],
            capture_output=True,
            text=True,
            timeout=30,  # Safety timeout
            env=os.environ.copy(),  # Pass environment variables
        )

        if result.returncode != 0:
            logger.error(f"Code execution error: {result.stderr}")
            return f"Error: {result.stderr}"

        return result.stdout.strip()

    except subprocess.TimeoutExpired:
        logger.error("Code execution timed out")
        return "Error: Execution timed out"
    except Exception as e:
        logger.error(f"Code execution failed: {e}")
        return f"Error: {str(e)}"
    finally:
        # Clean up temp file
        if os.path.exists(temp_name):
            try:
                os.unlink(temp_name)
            except Exception as e:
                logger.warning(f"Failed to delete temp file: {e}")
