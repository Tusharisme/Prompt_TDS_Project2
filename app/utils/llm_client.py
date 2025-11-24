import google.generativeai as genai
from loguru import logger
from app.config import settings

# Configure the Gemini API
try:
    genai.configure(api_key=settings.GEMINI_API_KEY)
except Exception as e:
    logger.error(f"Failed to configure Gemini API: {e}")

async def query_llm(prompt: str, model_name: str = "gemini-2.0-flash") -> str:
    """
    Sends a prompt to the Gemini LLM and returns the response text.
    Uses the GEMINI_API_KEY from settings.
    """
    try:
        model = genai.GenerativeModel(model_name)
        # Gemini calls are synchronous in the python SDK usually, but we can run them in a thread if needed.
        # For simplicity in this async context, we'll just call it directly. 
        # If it blocks too much, we can wrap in run_in_executor.
        
        response = model.generate_content(prompt)
        return response.text
    except Exception as e:
        logger.error(f"LLM query failed: {e}")
        return ""
