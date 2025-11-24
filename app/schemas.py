from pydantic import BaseModel, AnyHttpUrl, EmailStr, Field

class QuizRequest(BaseModel):
    email: EmailStr = Field(..., description="Student email ID")
    secret: str = Field(..., min_length=1, description="Secret provided via Google Form")
    url: AnyHttpUrl = Field(..., description="Quiz URL")

class ApiOK(BaseModel):
    ok: bool = True
    message: str
    echo: QuizRequest

class ApiError(BaseModel):
    ok: bool = False
    error: str
