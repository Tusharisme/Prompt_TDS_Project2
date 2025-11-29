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
import speech_recognition as sr
from pydub import AudioSegment

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

    # Initialize scratchpad temp file
    scratchpad_path = os.path.join(tempfile.gettempdir(), f"scratchpad_{os.getpid()}.txt")
    # Ensure it starts empty
    with open(scratchpad_path, "w", encoding="utf-8") as f:
        f.write("")

    driver = None
    try:
        driver = get_driver()
        current_url = task_url
        driver.get(current_url)

        last_observation = "Started quiz."

        # Track if we've already successfully submitted an answer
        has_submitted_successfully = False
        
        # Track the known submission URL to prevent "amnesia" between levels
        known_submission_url = None
        
        # Retry strategy: 3 approaches, each with 2 attempts (stability check)
        attempts_on_current_level = 0  # Number of distinct approaches tried (max 3)
        last_submitted_answer = None  # Track the last answer submitted
        consecutive_same_answer_count = 0  # Count duplicate submissions
        pending_soft_pass_url = None  # Store soft pass URL if offered

        # Limit steps to prevent infinite loops - REMOVED for production
        # We rely on the per-level retry logic and external timeouts
        step = 0
        from app.config import global_state  # Import state
        
        level_start_url = None
        
        while True:
            # Check for abort signal from main.py (concurrency safety)
            if global_state.abort_solver:
                logger.warning("Solver received ABORT signal. Exiting to allow new instance.")
                break

            logger.info(f"--- Step {step} ---")
            logger.info(f"Current URL: {driver.current_url}")
            
            # Track the URL where the level started
            if attempts_on_current_level == 0 and not level_start_url:
                 level_start_url = driver.current_url
                 logger.info(f"Level Start URL set to: {level_start_url}")
            elif attempts_on_current_level == 0:
                 # If we are at attempt 0 but level_start_url is set, it might be from previous loop
                 # Update it if we are on a new page (logic handled by reset below)
                 pass

            # Read scratchpad content
            try:
                with open(scratchpad_path, "r", encoding="utf-8") as f:
                    scratchpad_content = f.read()
            except Exception as e:
                scratchpad_content = f"Error reading scratchpad: {e}"

            # 1. Observe
            try:
                # Wait briefly for dynamic content
                await asyncio.sleep(1) 
                html_content = driver.page_source
                
                # Capture screenshot for Vision capabilities
                screenshot_b64 = driver.get_screenshot_as_base64()
                from PIL import Image
                from io import BytesIO
                import base64
                screenshot_image = Image.open(BytesIO(base64.b64decode(screenshot_b64)))
                
                # Use temp directory to avoid permission issues
                input_file_path = os.path.join(tempfile.gettempdir(), "input_page.html")
                with open(input_file_path, "w", encoding="utf-8") as f:
                    f.write(html_content)

            except Exception as e:
                logger.error(f"Failed to read page or capture screenshot: {e}")
                last_observation = f"Error observing page: {e}"
                continue

            # 2. Decide
            # If we've already submitted successfully, short‑circuit and tell the agent we're done
            if has_submitted_successfully:
                logger.info(
                    "Previous step already submitted successfully; stopping loop."
                )
                break

            decision = await get_agent_decision(
                html_content,
                driver.current_url,
                last_observation,
                email,
                secret,
                input_file_path,
                scratchpad_content,
                scratchpad_path,
                screenshot_image,
                known_submission_url,
                level_start_url,
            )
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
                    # Resolve relative URLs
                    from urllib.parse import urljoin
                    full_url = urljoin(driver.current_url, url)
                    
                    logger.info(f"Navigating to {full_url}")
                    driver.get(full_url)
                    last_observation = f"Navigated to {full_url}"
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
                if "email" not in payload:
                    payload["email"] = email
                if "secret" not in payload:
                    payload["secret"] = secret


                logger.info(f"Submitting to {submission_url} with payload: {payload}")

                async with httpx.AsyncClient() as client:
                    try:
                        resp = await client.post(submission_url, json=payload)
                        resp.raise_for_status()

                        try:
                            result = resp.json()
                            logger.info(f"Submission result: {result}")

                            if isinstance(result, dict) and result.get(
                                "correct", False
                            ):
                                # Success! Remember this URL for future levels
                                known_submission_url = submission_url
                                logger.info(f"Learned submission URL: {known_submission_url}")

                                next_url = result.get("url")
                                if next_url:
                                    driver.get(next_url)
                                    last_observation = f"Correct answer! Moving to next level: {next_url}"
                                    
                                    # Clear scratchpad for the new level to prevent state pollution
                                    try:
                                        with open(scratchpad_path, "w", encoding="utf-8") as f:
                                            f.write("")
                                        logger.info("Scratchpad cleared for next level.")
                                    except Exception as e:
                                        logger.warning(f"Failed to clear scratchpad: {e}")

                                    # Reset retry counters for new level
                                    attempts_on_current_level = 0
                                    last_submitted_answer = None
                                    consecutive_same_answer_count = 0
                                    # Reset retry counters for new level
                                    attempts_on_current_level = 0
                                    last_submitted_answer = None
                                    consecutive_same_answer_count = 0
                                    pending_soft_pass_url = None
                                    level_start_url = None # Reset for next level

                                    # We have a next level, so we are NOT done. 
                                    # Reset has_submitted_successfully so the loop continues for the new level.
                                    has_submitted_successfully = False 
                                else:
                                    last_observation = "Correct answer! No next URL provided. Maybe done?"
                                    has_submitted_successfully = True
                            else:
                                # Incorrect answer - implement retry strategy
                                current_answer = payload.get("answer")
                                next_url = result.get("url")
                                
                                # Store soft pass URL if provided
                                if next_url:
                                    pending_soft_pass_url = next_url
                                
                                # Check if this is the same answer as last time
                                if current_answer == last_submitted_answer:
                                    consecutive_same_answer_count += 1
                                    logger.info(f"Same answer submitted {consecutive_same_answer_count} times: {current_answer}")
                                    
                                    # If submitted same answer 2 times, this approach is confirmed failed
                                    if consecutive_same_answer_count >= 2:
                                        attempts_on_current_level += 1
                                        logger.info(f"Approach {attempts_on_current_level} failed (answer: {current_answer})")
                                        
                                        # Reset for next approach
                                        last_submitted_answer = None
                                        consecutive_same_answer_count = 0
                                        
                                        # Check if we've exhausted all 10 approaches
                                        if attempts_on_current_level >= 10:
                                            logger.info("All 10 approaches failed.")
                                            if pending_soft_pass_url:
                                                logger.info(f"Taking soft pass to: {pending_soft_pass_url}")
                                                driver.get(pending_soft_pass_url)
                                                last_observation = f"All approaches exhausted. Taking soft pass to: {pending_soft_pass_url}"
                                                
                                                # Clear scratchpad for the new level
                                                try:
                                                    with open(scratchpad_path, "w", encoding="utf-8") as f:
                                                        f.write("")
                                                    logger.info("Scratchpad cleared for next level.")
                                                except Exception as e:
                                                    logger.warning(f"Failed to clear scratchpad: {e}")
                                                
                                                # Reset retry counters for new level
                                                attempts_on_current_level = 0
                                                last_submitted_answer = None
                                                consecutive_same_answer_count = 0
                                                # Reset retry counters for new level
                                                attempts_on_current_level = 0
                                                last_submitted_answer = None
                                                consecutive_same_answer_count = 0
                                                pending_soft_pass_url = None
                                                has_submitted_successfully = False
                                                level_start_url = None # Reset for next level
                                            else:
                                                logger.info("No soft pass URL available. Stopping.")
                                                has_submitted_successfully = True
                                                break
                                        else:
                                            last_observation = f"Incorrect answer. Try a different approach. (Attempt {attempts_on_current_level}/10 failed)"
                                    else:
                                        last_observation = f"Incorrect answer: {current_answer}. Submit again to confirm approach, or try a different method."
                                else:
                                    # New answer - track it
                                    last_submitted_answer = current_answer
                                    consecutive_same_answer_count = 1
                                    last_observation = f"Incorrect answer: {current_answer}. You can retry with the same answer to confirm this approach, or try a different method."

                        except ValueError:
                            # Response is not JSON, but status is 2xx (success)
                            logger.info(
                                f"Submission successful (non-JSON). Status: {resp.status_code}"
                            )
                            last_observation = f"Submission successful! Server returned status {resp.status_code}. Response: {resp.text[:200]}"
                            has_submitted_successfully = True

                    except Exception as e:
                        # Detailed logging to debug empty error messages
                        logger.error(
                            f"Submission failed with exception type: {type(e).__name__}"
                        )
                        logger.error(f"Exception repr: {repr(e)}")
                        logger.error(f"Exception str: {str(e)}")

                        # If we got here but status code was 2xx, it might be a weird JSON error not caught by ValueError
                        if "resp" in locals() and 200 <= resp.status_code < 300:
                            logger.info(
                                f"Submission likely successful despite error. Status: {resp.status_code}"
                            )
                            last_observation = f"Submission successful! Server returned status {resp.status_code}. Response text: {resp.text[:200]}"
                            has_submitted_successfully = True
                        else:
                            last_observation = (
                                f"Submission failed: {type(e).__name__}: {str(e)}"
                            )
                            
                            # Increment attempts on exception to prevent infinite loops
                            attempts_on_current_level += 1
                            logger.warning(f"Submission exception. Attempt {attempts_on_current_level}/10 failed.")
                            
                            if attempts_on_current_level >= 10:
                                logger.info("All 10 approaches failed (due to exceptions).")
                                if pending_soft_pass_url:
                                    logger.info(f"Taking soft pass to: {pending_soft_pass_url}")
                                    driver.get(pending_soft_pass_url)
                                    last_observation = f"All approaches exhausted (exceptions). Taking soft pass to: {pending_soft_pass_url}"
                                    
                                    # Clear scratchpad
                                    try:
                                        with open(scratchpad_path, "w", encoding="utf-8") as f:
                                            f.write("")
                                    except Exception as e:
                                        logger.warning(f"Failed to clear scratchpad: {e}")
                                    
                                    # Reset counters
                                    attempts_on_current_level = 0
                                    last_submitted_answer = None
                                    consecutive_same_answer_count = 0
                                    pending_soft_pass_url = None
                                    has_submitted_successfully = False
                                else:
                                    logger.info("No soft pass URL available. Stopping.")
                                    has_submitted_successfully = True
                                    break

                    # If we know submission was successful and there is no explicit next level,
                    # rely on has_submitted_successfully flag to stop further decisions.


            elif action == "done":
                logger.info("Agent decided the task is complete.")
                break

            else:
                logger.warning(f"Unknown action: {action}")
                last_observation = f"Error: Unknown action '{action}'"
            
            # Increment step counter manually since we are in a while True loop
            step += 1

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
    html_content: str,
    current_url: str,
    last_observation: str,
    email: str,
    secret: str,
    input_file_path: str,
    scratchpad_content: str,
    scratchpad_path: str,
    screenshot_image=None,
    known_submission_url: str = None,
    level_start_url: str = None,
) -> dict:
    """
    Asks the LLM for the next step based on the current state and visual context.
    """
    # Clean HTML to save tokens
    cleaned_html = clean_html(html_content)
    # Truncate if still too long (safety net)
    if len(cleaned_html) > 50000:
        cleaned_html = cleaned_html[:50000] + "...(truncated)"

    # Detect and download audio
    audio_file_path = None
    soup = BeautifulSoup(html_content, "html.parser")
    audio_tag = soup.find("audio")
    if audio_tag:
        # Check for direct src attribute first
        audio_src = audio_tag.get("src")
        
        # If not found, check for <source> child element
        if not audio_src:
            source_tag = audio_tag.find("source")
            if source_tag:
                audio_src = source_tag.get("src")
        
        if audio_src:
            logger.info(f"Found audio source: {audio_src}")
        
        # Handle relative URLs
        if not audio_src.startswith("http"):
            from urllib.parse import urljoin
            audio_src = urljoin(current_url, audio_src)
            
        # Download audio to /tmp
        try:
            import requests
            import os
            
            # Create a unique filename based on the URL hash or just a timestamp
            import hashlib
            file_hash = hashlib.md5(audio_src.encode()).hexdigest()
            ext = ".mp3" if ".mp3" in audio_src else ".wav" # Simple extension guess
            audio_file_path = f"/tmp/audio_{file_hash}{ext}"
            
            if not os.path.exists(audio_file_path):
                logger.info(f"Downloading audio from {audio_src} to {audio_file_path}...")
                resp = requests.get(audio_src, timeout=30)
                resp.raise_for_status()
                with open(audio_file_path, "wb") as f:
                    f.write(resp.content)
                logger.info("Audio download successful.")
            else:
                logger.info("Audio file already exists in /tmp, using cached version.")
                
        except Exception as e:
            logger.error(f"Failed to download audio: {e}")
            audio_file_path = None
    
    system_prompt = f"""
    You are an autonomous AI agent solving a quiz/CTF challenge.
    
    # OBJECTIVE
    Navigate the website, find the answer to the question, and submit it.
    
    # INPUTS
    - Current URL: {current_url}
    - Last Observation: {last_observation}
    - Scratchpad (Memory):
    ```text
    {scratchpad_content}
    ```
    - HTML Content: (Provided below)
    - Visual Context: (Screenshot provided)
    - Audio Context: {"(Audio file provided)" if audio_file_path else "(No audio detected)"}
    - Known Submission URL: {known_submission_url if known_submission_url else "(Not yet discovered)"} (Use this ONLY if the current page does not provide a specific submission URL)
    
    # CRITICAL INSTRUCTIONS
    0. **ACTION PRIORITY (PREVENT LOOPS)**:
       - **IF ANSWER IN SCRATCHPAD -> SUBMIT**: If you have calculated the answer and wrote it to the scratchpad, your **ONLY** allowed action is `submit`.
       - **DO NOT VERIFY AGAIN**: Do not run code to "double check" if you already have the answer. Infinite verification loops are a failure.
    1. **MEMORY**: Use your Scratchpad! 
       - If you calculate an answer, WRITE IT to the scratchpad immediately using `execute_code`.
       - Example: `with open(r"{scratchpad_path}", "a") as f: f.write("Answer: 495759\\n")`
       - This prevents you from forgetting the answer if you navigate to a new page.
    2. **VISION**: You have access to a screenshot. Use it for graphs/charts.
    3. **AUDIO**: You can HEAR! 
       - If an audio file is provided, analyze the speech/sound to find the secret code.
       - The answer is often spoken directly.
    4. **NO GUESSING URLS**: Do NOT guess submission URLs.
    5. **JSON/DATA**: Use Python to download/process JSON files.
    6. **SUBMISSION**: 
       - If you have the answer, use the `submit` action.
       - Payload must include: `email`, `secret`, `url`, `answer`.
       - `email`: "{email}", `secret`: "{secret}".
    ```python
    # ✅ GOOD: Extract from HTML
    from bs4 import BeautifulSoup
    import json
    
    with open(r"{input_file_path}", "r", encoding="utf-8") as f:
        html = f.read()
    
    soup = BeautifulSoup(html, "html.parser")
    
    # Find the <pre> tag containing JSON data
    pre_tag = soup.find("pre", id="city-data")  # Adjust selector as needed
    if pre_tag:
        json_text = pre_tag.get_text()
        city_data = json.loads(json_text)
        print("Extracted data:", city_data)  # Always verify!
    ```
    
    **WHY THIS MATTERS:**
    - The actual data on the server may differ slightly from what you see
    - Even tiny differences (0.0001) can change calculation results
       - **Hypothesis 1**: Wrong column? Print `df.columns`.
       - **Hypothesis 2**: Dirty data? Print `df['col'].unique()[:10]`.
       - **Hypothesis 3**: Header issue? Try `pd.read_csv(..., header=None)`.

    # DOMAIN AWARENESS & LOGIC
    1. **CONTEXTUAL LOGIC**: Apply the correct scientific/mathematical model for the data type.
       - **Lat/Lon Coordinates**: Implies **Geodesic Distance** (Haversine). Euclidean is wrong.
       - **Cryptography**: Look for standard algorithms (AES, SHA, RSA).
       - **Physics/Math**: Check for specific formulas or constants related to the problem.
       - Print the first 5 rows (`df.head()`) to verify alignment.

    # SMART DEBUGGING STRATEGY (WHEN EXTRACTION FAILS)
    - **STOP GUESSING**: If your regex fails, DO NOT guess a new one immediately.
    - **INSPECT FIRST**: Use `execute_code` to PRINT the content around the keyword.
      ```python
      # Example: Find where "Data Dump" is and print context
      with open(r"{input_file_path}", "r") as f: html = f.read()
      idx = html.find("Data Dump")
      if idx != -1:
          print(f"CONTEXT: {{html[idx:idx+500]}}")
      else:
          print("Keyword 'Data Dump' not found.")
      ```
    - **SELF-CORRECT**: Use the printed context to write the correct regex in the next step.

    # UNIVERSAL DATA INSPECTION (CRITICAL)
    **NEVER assume data structure.** APIs change.
    **ALWAYS INSPECT FIRST**:
        *   If it's a list: Print `len(data)` and `data[0]`.
        *   If it's a dict: Print `data.keys()`.
        *   If it's a string: Print the first 500 chars.
    **EXTRACTION WORKFLOW**:
        1. Read HTML from `{input_file_path}`
        2. Use BeautifulSoup to find the data element
        3. Extract the text/content
        4. **Print it to verify correctness**
        5. Parse (JSON/CSV/regex)
        6. **Print parsed result to verify**
        7. Perform calculation
        8. Submit answer
    **STABILITY CHECK**:
        *   If you calculate the **SAME ANSWER TWICE** in a row -> **SUBMIT IT IMMEDIATELY**.
        *   **DO NOT VERIFY A THIRD TIME.** Infinite verification loops are a failure.
        *   Trust your result if it repeats.

    # VERIFICATION CHECKLIST
    *   **USE LEVEL START URL**: When constructing the JSON payload, the "url" field MUST be the `Level Start URL` provided in the user message, NOT the `Current URL`, unless they are the same. The server expects the URL where the problem started.
    *   **EXPLORE IF NEEDED**: If the current page doesn't have a submission form or the answer, look for links like "Submit", "Next", "Quiz", or "API" and navigate to them.
    *   **TRUST YOUR SCRATCHPAD**: If you have calculated an answer and saved it to the scratchpad, SUBMIT IT. Do not re-calculate unless you are sure it is wrong.
    *   **HANDLE 400/500 ERRORS**: If a submission fails with a 400 or 500 error, DO NOT resubmit the same payload. Check your JSON structure, field names, and data types.
    *   **Extract, Don't Hardcode**: Did you extract ALL data from HTML using BeautifulSoup?
    *   **Print to Verify**: Did you print extracted data before using it?
    *   **Filter Data**: Did you remove nulls/None?
    *   **Clean Strings**: Did you remove currency symbols ($), commas (,), or extra spaces?
    *   **Type Conversion**: Did you convert strings to floats/ints before math?
    *   **Edge Cases**: Did you handle empty lists or missing keys?
    *   **Double Check**: If the answer seems too simple or too complex, re-read the HTML instructions.
    
    # TOOLS
    1. `navigate(url)`: Go to a URL.
    2. `execute_code(code)`: Run Python code. 
       - HTML file: `{input_file_path}`
       - Scratchpad file: `{scratchpad_path}` (Read/Write allowed)
    3. `submit(submission_url, payload)`: Submit the answer.
    
    # OUTPUT FORMAT
    You MUST respond in this EXACT XML-style format:
    
    <thought>
    Your reasoning here. Explain what you see in the screenshot and HTML.
    </thought>
    <action>navigate OR execute_code OR submit</action>
    <url>URL to navigate to (only for navigate)</url>
    <code>
    Python code to execute (only for execute_code)
    </code>
    <submission_url>URL to submit to (only for submit)</submission_url>
    <payload>
    JSON payload for submission (only for submit)
    </payload>
    """
    

    
    user_message = f"Current URL: {current_url}\nLevel Start URL: {level_start_url} (Use this for 'url' in submission payload)\nLast Observation: {last_observation}\nScratchpad:\n{scratchpad_content}\n\nHTML Snippet (first 2000 chars):\n{cleaned_html[:2000]}..."

    try:
        # Prepare the content list for Gemini (Multimodal)
        contents = [system_prompt, user_message]
        if screenshot_image:
            contents.append(screenshot_image)
        if audio_file_path:
            contents.append(audio_file_path)

        # Use the shared utility function
        response_text = await query_llm(contents)
        logger.info(f"Raw LLM Response: {response_text}") # Added logging
        
        # Parse XML-style output
        import re
        
        thought_match = re.search(r"<thought>(.*?)</thought>", response_text, re.DOTALL)
        action_match = re.search(r"<action>(.*?)</action>", response_text, re.DOTALL)
        
        decision = {}
        if thought_match:
            decision["thought"] = thought_match.group(1).strip()
        if action_match:
            decision["action"] = action_match.group(1).strip()
            
        if decision.get("action") == "navigate":
            url_match = re.search(r"<url>(.*?)</url>", response_text, re.DOTALL)
            if url_match:
                decision["url"] = url_match.group(1).strip()
                
        elif decision.get("action") == "execute_code":
            code_match = re.search(r"<code>(.*?)</code>", response_text, re.DOTALL)
            if code_match:
                decision["code"] = code_match.group(1).strip()
                
        elif decision.get("action") == "submit":
            sub_url_match = re.search(r"<submission_url>(.*?)</submission_url>", response_text, re.DOTALL)
            payload_match = re.search(r"<payload>(.*?)</payload>", response_text, re.DOTALL)
            
            if sub_url_match:
                decision["submission_url"] = sub_url_match.group(1).strip()
            if payload_match:
                payload_str = payload_match.group(1).strip()
                # Use json_repair for the payload part
                import json_repair
                decision["payload"] = json_repair.loads(payload_str)

        return decision

    except Exception as e:
        logger.error(f"LLM Error: {e}")
        return None


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
