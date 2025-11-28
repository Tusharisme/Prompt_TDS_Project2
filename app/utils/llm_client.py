import google.generativeai as genai
from loguru import logger
from app.config import settings

# Configure the Gemini API
try:
    genai.configure(api_key=settings.GEMINI_API_KEY)
except Exception as e:
    logger.error(f"Failed to configure Gemini API: {e}")

import asyncio
import httpx
from tenacity import retry, stop_after_attempt, wait_exponential, retry_if_exception_type
import google.api_core.exceptions

# Define retry strategy for primary model
# Wait 2^x * 1 second between retries, up to 10 seconds, max 5 attempts
retry_strategy = retry(
    stop=stop_after_attempt(5),
    wait=wait_exponential(multiplier=1, min=2, max=10),
    retry=retry_if_exception_type((
        google.api_core.exceptions.ResourceExhausted,
        google.api_core.exceptions.ServiceUnavailable,
        google.api_core.exceptions.InternalServerError
    )),
    reraise=True # Reraise exception so we can catch it and switch to fallback
)

@retry_strategy
async def _query_primary_gemini(model, contents):
    """
    Helper function to query primary Gemini with retry logic.
    """
    return await model.generate_content_async(contents)

async def query_llm(contents: list | str, model_name: str = "gemini-2.0-flash-exp") -> str:
    """
    Sends a prompt (text or list of text/images) to the Gemini LLM and returns the response text.
    Uses the GEMINI_API_KEY from settings.
    Falls back to AI Pipe if primary fails.
    """
    # Ensure contents is in the correct format
    if isinstance(contents, str):
        contents = [contents]

    # 1. Try Primary Gemini API
    try:
        model = genai.GenerativeModel(model_name)
        response = await _query_primary_gemini(model, contents)
        return response.text
    except Exception as e:
        logger.warning(f"Primary Gemini API failed after retries: {e}")
        
        # 2. Try Fallback: AI Pipe
        if settings.AIPIPE_TOKEN:
            logger.info("Attempting fallback to AI Pipe...")
            try:
                return await _query_aipipe(contents, model_name)
            except Exception as fallback_e:
                logger.error(f"AI Pipe fallback also failed: {fallback_e}")
        else:
            logger.error("No AIPIPE_TOKEN configured for fallback.")
            
        return ""

async def _query_aipipe(contents: list, model_name: str) -> str:
    """
    Helper to query AI Pipe API.
    """
    url = f"https://aipipe.org/geminiv1beta/models/{model_name}:generateContent"
    headers = {
        "x-goog-api-key": settings.AIPIPE_TOKEN,
        "Content-Type": "application/json"
    }
    
    # Convert contents to Gemini JSON format
    # contents is a list of strings (text) or PIL Images
    parts = []
    for item in contents:
        if isinstance(item, str):
            parts.append({"text": item})
        elif hasattr(item, "save"): # Check if it's a PIL Image
            import io
            import base64
            buffered = io.BytesIO()
            item.save(buffered, format="JPEG")
            img_str = base64.b64encode(buffered.getvalue()).decode("utf-8")
            parts.append({
                "inline_data": {
                    "mime_type": "image/jpeg",
                    "data": img_str
                }
            })
            
    payload = {
        "contents": [{"parts": parts}]
    }
    
    async with httpx.AsyncClient() as client:
        response = await client.post(url, headers=headers, json=payload, timeout=60.0)
        response.raise_for_status()
        result = response.json()
        
        # Extract text from response
        # Structure: { "candidates": [{ "content": { "parts": [{ "text": "..." }] } }] }
        if "candidates" in result and result["candidates"]:
            parts = result["candidates"][0].get("content", {}).get("parts", [])
            return "".join([p.get("text", "") for p in parts])
            
        return ""
