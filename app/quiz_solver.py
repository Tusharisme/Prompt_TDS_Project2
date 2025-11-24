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
from bs4 import BeautifulSoup
from loguru import logger
from app.config import settings
from app.utils.llm_client import query_llm

# Selenium imports
from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.chrome.service import Service
from webdriver_manager.chrome import ChromeDriverManager

def get_driver():
    """
    Initializes a headless Chrome driver.
    """
    chrome_options = Options()
    chrome_options.add_argument("--headless")
    chrome_options.add_argument("--no-sandbox")
    chrome_options.add_argument("--disable-dev-shm-usage")
    # Important for running in Docker/Cloud environments
    chrome_options.add_argument("--disable-gpu")
    chrome_options.add_argument("--window-size=1920,1080")
    
    try:
        service = Service(ChromeDriverManager().install())
        driver = webdriver.Chrome(service=service, options=chrome_options)
        return driver
    except Exception as e:
        logger.error(f"Failed to initialize Selenium driver: {e}")
        raise

async def solve_quiz(task_url: str, email: str, secret: str):
    """
    Main loop to solve the quiz.
    Fetches the quiz using Selenium (for dynamic content), solves it, 
    submits the answer, and handles the next URL.
    """
    current_url = task_url
    driver = None
    
    try:
        # Initialize driver once
        driver = get_driver()
        
        while current_url:
            logger.info(f"Solving quiz at: {current_url}")
            
            # 1. Fetch the quiz page using Selenium
            try:
                driver.get(current_url)
                # Wait a bit for dynamic content if needed? 
                # For now, implicit wait or just proceeding is usually enough for simple JS.
                # driver.implicitly_wait(2) 
                html_content = driver.page_source
            except Exception as e:
                logger.error(f"Failed to fetch quiz with Selenium: {e}")
                break

            # 2. Extract the question
            question = extract_question(html_content)
            print(f"DEBUG: Extracted question: {question}")
            if not question:
                logger.error("Could not extract question from page.")
                break
                
            logger.info(f"Question: {question}")

            # 3. Solve the question
            answer = await get_answer(question)
            logger.info(f"Computed Answer: {answer}")

            # 4. Submit the answer
            # We still use httpx for submission as it's an API call, not a page navigation
            submission_url = "https://tds-llm-analysis.s-anand.net/submit" 
            
            # Try to extract submission URL from text if present
            submit_url_match = re.search(r'Post your answer to (https://[^\s]+)', html_content)
            if submit_url_match:
                submission_url = submit_url_match.group(1)
            
            payload = {
                "email": email,
                "secret": secret,
                "url": current_url,
                "answer": answer
            }
            
            print(f"DEBUG: Submitting to {submission_url} with payload: {payload}")
            logger.info(f"Submitting to {submission_url} with payload: {payload}")

            async with httpx.AsyncClient() as client:
                try:
                    resp = await client.post(submission_url, json=payload)
                    resp.raise_for_status()
                    result = resp.json()
                    print(f"DEBUG: Submission result: {result}")
                    logger.info(f"Submission result: {result}")
                    
                    if result.get("correct", False):
                        current_url = result.get("url") # Next URL
                        print(f"DEBUG: Next URL: {current_url}")
                    else:
                        logger.warning(f"Incorrect answer: {result.get('reason')}")
                        current_url = result.get("url") 
                        print(f"DEBUG: Incorrect answer. Next URL: {current_url}")
                        
                except Exception as e:
                    print(f"DEBUG: Submission failed: {e}")
                    logger.error(f"Submission failed: {e}")
                    break
                    
    except Exception as e:
        logger.error(f"Fatal error in solver loop: {e}")
    finally:
        if driver:
            driver.quit()

def extract_question(html: str) -> str:
    """
    Extracts the question text from the HTML.
    Handles `atob` pattern and falls back to visible text.
    """
    soup = BeautifulSoup(html, "html.parser")
    
    # 1. Look for script tag with atob (common in this challenge)
    scripts = soup.find_all("script")
    for script in scripts:
        if script.string and "atob" in script.string:
            match = re.search(r'atob\([`\'"]([^`\'"]+)[`\'"]\)', script.string)
            if match:
                b64_str = match.group(1)
                # Fix padding
                b64_str += "=" * ((4 - len(b64_str) % 4) % 4)
                try:
                    decoded = base64.b64decode(b64_str).decode("utf-8")
                    return decoded
                except Exception as e:
                    logger.error(f"Base64 decode failed: {e}")
    
    # 2. Look for specific container (often <div id="question"> or similar?)
    # For now, fallback to all text, but maybe clean it up
    text = soup.get_text(separator=" ", strip=True)
    return text

async def get_answer(question: str):
    """
    Decides how to solve the question (LLM direct vs Code) and returns the answer.
    """
    prompt = f"""
    You are an intelligent assistant solving a data analysis quiz.
    Question: {question}
    
    If the question requires downloading a file and analyzing it, write a Python script to do so.
    The script should:
    1. Download the file (using requests/httpx).
    2. Process it (using pandas/numpy).
    3. Print the final answer to stdout.
    
    If the question is a simple knowledge question, just provide the answer directly.
    
    Output format:
    If code is needed, wrap it in ```python ... ```.
    If direct answer, just write the answer.
    """
    
    response = await query_llm(prompt)
    
    # Check for code block
    code_match = re.search(r'```python(.*?)```', response, re.DOTALL)
    if code_match:
        code = code_match.group(1).strip()
        logger.info("Executing generated code...")
        return execute_code(code)
    else:
        return response.strip()

def execute_code(code: str):
    """
    Executes the given Python code using subprocess for better isolation.
    Writes code to a temp file and runs it.
    """
    # Create a temporary file
    with tempfile.NamedTemporaryFile(mode='w', suffix='.py', delete=False, encoding='utf-8') as f:
        f.write(code)
        temp_name = f.name
    
    try:
        # Run the code
        # We use sys.executable to ensure we use the same python interpreter (with installed packages)
        result = subprocess.run(
            [sys.executable, temp_name],
            capture_output=True,
            text=True,
            timeout=30 # Safety timeout
        )
        
        if result.returncode != 0:
            logger.error(f"Code execution error: {result.stderr}")
            return None
            
        return result.stdout.strip()
        
    except subprocess.TimeoutExpired:
        logger.error("Code execution timed out")
        return None
    except Exception as e:
        logger.error(f"Code execution failed: {e}")
        return None
    finally:
        # Clean up temp file
        if os.path.exists(temp_name):
            try:
                os.unlink(temp_name)
            except Exception as e:
                logger.warning(f"Failed to delete temp file: {e}")
