from pydantic_settings import BaseSettings
from pydantic import AnyHttpUrl
from typing import List, Optional

class Settings(BaseSettings):
    # Required by the project spec
    STUDENT_EMAIL: str
    STUDENT_SECRET: str
    STUDENT_SECRET: str
    GEMINI_API_KEY: str
    AIPIPE_TOKEN: Optional[str] = None

    # CORS
    CORS_ORIGINS: Optional[str] = ""  # comma-separated
    CORS_ALLOW_CREDENTIALS: bool = True
    CORS_ALLOW_HEADERS: str = "*"
    CORS_ALLOW_METHODS: str = "*"

    # Service
    APP_NAME: str = "LLM Analysis Quiz - Phase 1"
    APP_ENV: str = "production"

    class Config:
        env_file = ".env"
        case_sensitive = True

    @property
    def cors_origins_list(self) -> List[str]:
        if not self.CORS_ORIGINS:
            return []
        return [o.strip() for o in self.CORS_ORIGINS.split(",") if o.strip()]

settings = Settings()
