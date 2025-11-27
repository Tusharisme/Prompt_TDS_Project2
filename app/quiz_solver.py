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
