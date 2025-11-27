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
        return driver
    except Exception as e:
        logger.error(f"Failed to initialize Selenium driver: {e}")
        raise

async def solve_quiz(task_url: str, email: str, secret: str):
    """
    Agentic loop to solve the quiz.
    Uses an Observe-Decide-Act cycle powered by the LLM.
    """
    # Explicitly cast to string to handle Pydantic types (AnyHttpUrl, EmailStr)
    task_url = str(task_url)
    email = str(email)
    secret = str(secret)
    
    driver = None
    try:
        driver = get_driver()
        current_url = task_url
        driver.get(current_url)
        
        last_observation = "Started quiz."
        
        # Limit steps to prevent infinite loops
        for step in range(25):
            logger.info(f"--- Step {step} ---")
            logger.info(f"Current URL: {driver.current_url}")
            
            # 1. Observe
            try:
                # Wait briefly for dynamic content
                await asyncio.sleep(1) 
                html_content = driver.page_source
                
                # Save HTML to a file for the agent to read
                # Use temp directory to avoid permission issues
                input_file_path = os.path.join(tempfile.gettempdir(), "input_page.html")
                with open(input_file_path, "w", encoding="utf-8") as f:
                    f.write(html_content)
                    
            except Exception as e:
                logger.error(f"Failed to read page: {e}")
                last_observation = f"Error reading page: {e}"
                continue

            # 2. Decide
            decision = await get_agent_decision(html_content, driver.current_url, last_observation, email, secret, input_file_path)
            logger.info(f"Agent Decision: {decision}")
            
            if not decision:
                logger.error("Agent returned no decision.")
                break
                
            action = decision.get("action")
            reasoning = decision.get("thought")
            logger.info(f"Reasoning: {reasoning}")
            
            # 3. Act
            if action == "navigate":
                url = decision.get("url")
                if url:
                    logger.info(f"Navigating to {url}")
                    driver.get(url)
                    last_observation = f"Navigated to {url}"
                else:
                    last_observation = "Error: 'navigate' action missing 'url'."
                    
            elif action == "execute_code":
                code = decision.get("code")
                if code:
                    logger.info("Executing code...")
                    output = execute_code(code)
                    logger.info(f"Code Output: {output}")
                    last_observation = f"Code Execution Result:\n{output}"
                else:
                    last_observation = "Error: 'execute_code' action missing 'code'."
                    
            elif action == "submit":
                submission_url = decision.get("submission_url")
                payload = decision.get("payload", {})
                
                # Ensure email/secret are present if not provided by LLM (though LLM should provide them)
                if "email" not in payload: payload["email"] = email
                if "secret" not in payload: payload["secret"] = secret
                
                logger.info(f"Submitting to {submission_url} with payload: {payload}")
                
                async with httpx.AsyncClient() as client:
                    try:
                        resp = await client.post(submission_url, json=payload)
                        resp.raise_for_status()
                        
                        try:
                            result = resp.json()
                            logger.info(f"Submission result: {result}")
                            
                            if isinstance(result, dict) and result.get("correct", False):
                                next_url = result.get("url")
                                if next_url:
                                    driver.get(next_url)
                                    last_observation = f"Correct answer! Moving to next level: {next_url}"
                                else:
                                    last_observation = "Correct answer! No next URL provided. Maybe done?"
                            else:
                                # Handle JSON response that doesn't strictly follow expected schema
                                last_observation = f"Submission successful. Server response: {result}"
                                
                        except ValueError:
                            # Response is not JSON, but status is 2xx (success)
                            logger.info(f"Submission successful (non-JSON). Status: {resp.status_code}")
                            last_observation = f"Submission successful! Server returned status {resp.status_code}. Response: {resp.text[:200]}"
                            
                    except Exception as e:
                        logger.error(f"Submission failed: {e}")
                        last_observation = f"Submission failed: {e}"
                        
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

def clean_html(html: str) -> str:
    """
    Cleans HTML to reduce token count while preserving relevant content.
    Removes styles, SVGs, and unnecessary attributes.
    """
    soup = BeautifulSoup(html, "html.parser")
    
    # Remove irrelevant tags
    for tag in soup(["style", "svg", "path", "link", "meta", "noscript", "iframe", "footer", "header"]):
        tag.decompose()
        
    # Comments are PRESERVED as they often contain hidden clues for the agent
    # for element in soup(text=lambda text: isinstance(text, Comment)):
    #     element.extract()
        
    # Clean attributes
    for tag in soup.find_all(True):
        # Keep only essential attributes
        allowed_attrs = ['id', 'name', 'class', 'href', 'src', 'action', 'method', 'type', 'value', 'placeholder']
        attrs = dict(tag.attrs)
        for attr in attrs:
            if attr not in allowed_attrs:
                del tag.attrs[attr]
                
        # Truncate long class names or src (optional, but good for safety)
        if 'src' in tag.attrs and len(tag['src']) > 500:
            tag['src'] = tag['src'][:500] + "..."
            
    return str(soup)

async def get_agent_decision(html: str, url: str, last_observation: str, email: str, secret: str, input_file_path: str) -> dict:
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
    3. **SUBMIT THE ANSWER**: Once you have calculated the answer, DO NOT just print it. You MUST use the "submit" action to send it to the correct URL.
    
    Example of reading the file correctly:
    ```python
    import re
    import os
    
    # Read the HTML file
    with open(r"{input_file_path}", "r", encoding="utf-8") as f:
        html = f.read()
    # ... extract data from html ...
    ```

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
    
    Instructions:
    - Look for hidden questions (e.g., in `atob` scripts, comments, or visible text).
    - If you need to calculate something or download a file, use "execute_code".
    - If you have the answer, look for a submission form or endpoint in the HTML and use "submit".
    - The submission payload usually requires "email", "secret", "url" (current task url), and "answer".
    - Return ONLY a valid JSON object with the following schema:
    
    {{
        "thought": "Your reasoning here...",
        "action": "navigate" | "execute_code" | "submit" | "done",
        "url": "..." (if action is navigate),
        "code": "..." (if action is execute_code),
        "submission_url": "..." (if action is submit),
        "payload": {{ ... }} (if action is submit)
    }}
    """
    
    response = await query_llm(prompt)
    
    # Clean up response to ensure JSON
    try:
        # Strip markdown code blocks if present
        if "```json" in response:
            response = response.split("```json")[1].split("```")[0]
        elif "```" in response:
            response = response.split("```")[1].split("```")[0]
            
        return json.loads(response.strip())
    except Exception as e:
        logger.error(f"Failed to parse LLM decision: {e}. Response: {response}")
        return {"thought": "Failed to parse JSON", "action": "done"}

def execute_code(code: str):
    """
    Executes the given Python code using subprocess for better isolation.
    Writes code to a temp file and runs it.
    Passes current environment variables (including API keys) to the subprocess.
    """
    # Create a temporary file
    with tempfile.NamedTemporaryFile(mode='w', suffix='.py', delete=False, encoding='utf-8') as f:
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
            timeout=30, # Safety timeout
            env=os.environ.copy() # Pass environment variables
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
