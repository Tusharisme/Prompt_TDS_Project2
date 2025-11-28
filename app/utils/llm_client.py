import google.generativeai as genai
from loguru import logger
from app.config import settings

# Configure the Gemini API
try:
    genai.configure(api_key=settings.GEMINI_API_KEY)
except Exception as e:
    logger.error(f"Failed to configure Gemini API: {e}")

async def query_llm(contents: list | str, model_name: str = "gemini-2.0-flash-exp") -> str:
    """
    Sends a prompt (text or list of text/images) to the Gemini LLM and returns the response text.
    Uses the GEMINI_API_KEY from settings.
    """
    try:
        model = genai.GenerativeModel(model_name)
        
        # Ensure contents is in the correct format
        if isinstance(contents, str):
            contents = [contents]
            
        # Use async generation
        response = await model.generate_content_async(contents)
        return response.text
    except Exception as e:
        logger.error(f"LLM query failed: {e}")
        return ""
