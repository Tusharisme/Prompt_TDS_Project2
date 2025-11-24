import json
import sys
from typing import Dict

from fastapi import FastAPI, Request, status, BackgroundTasks
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, FileResponse

from loguru import logger
from starlette.exceptions import HTTPException as StarletteHTTPException
from fastapi.exceptions import RequestValidationError

from app.schemas import QuizRequest, ApiOK, ApiError
from app.config import settings
from app.quiz_solver import solve_quiz

# -------- Logging (loguru) --------
# Remove default handler and add ours with useful format
logger.remove()
logger.add(
    sys.stdout,
    colorize=True,
    level="INFO",
    format="<green>{time:YYYY-MM-DD HH:mm:ss}</green> | "
    "<level>{level: <8}</level> | "
    "<cyan>{name}</cyan>:<cyan>{function}</cyan>:<cyan>{line}</cyan> - "
    "<level>{message}</level>",
)

app = FastAPI(
    title=settings.APP_NAME,
    version="1.0.0",
    docs_url="/docs",
    redoc_url="/redoc",
)

# -------- CORS --------
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins_list
    or ["*"],  # default open; tighten in prod by setting CORS_ORIGINS
    allow_credentials=settings.CORS_ALLOW_CREDENTIALS,
    allow_methods=(
        [m.strip() for m in settings.CORS_ALLOW_METHODS.split(",")]
        if settings.CORS_ALLOW_METHODS != "*"
        else ["*"]
    ),
    allow_headers=(
        [h.strip() for h in settings.CORS_ALLOW_HEADERS.split(",")]
        if settings.CORS_ALLOW_HEADERS != "*"
        else ["*"]
    ),
)


# -------- Health --------
@app.get("/health", tags=["meta"])
async def health():
    return {"ok": True, "service": settings.APP_NAME, "env": settings.APP_ENV}


@app.get("/", include_in_schema=False)
async def root():
    """Serve the frontend test interface"""
    return FileResponse("index.html")


# -------- Error Handlers --------
@app.exception_handler(RequestValidationError)
async def validation_exception_handler(request: Request, exc: RequestValidationError):
    logger.warning(f"400 Validation error at {request.url} :: {exc.errors()}")
    return JSONResponse(
        status_code=status.HTTP_400_BAD_REQUEST,
        content=ApiError(ok=False, error="Invalid JSON payload").dict(),
    )


@app.exception_handler(StarletteHTTPException)
async def http_exception_handler(request: Request, exc: StarletteHTTPException):
    logger.error(f"{exc.status_code} HTTP error at {request.url} :: {exc.detail}")
    return JSONResponse(
        status_code=exc.status_code,
        content=ApiError(ok=False, error=str(exc.detail)).dict(),
    )


@app.exception_handler(Exception)
async def unhandled_exception_handler(request: Request, exc: Exception):
    logger.exception(f"500 Unhandled error at {request.url}")
    return JSONResponse(
        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        content=ApiError(ok=False, error="Internal Server Error").dict(),
    )


# -------- Core Route (Phase 1) --------

@app.post(
    "/quiz",
    response_model=ApiOK,
    responses={400: {"model": ApiError}, 403: {"model": ApiError}},
)
async def accept_quiz(payload: QuizRequest, request: Request, background_tasks: BackgroundTasks):
    """
    Phase 1 behavior: Validate JSON shape & secret.
    Phase 2 behavior: Trigger background solver.
    """
    client_ip = request.client.host if request.client else "unknown"
    logger.info(
        f"Incoming /quiz from {client_ip}: email={payload.email}, url={payload.url}"
    )

    if payload.secret != settings.STUDENT_SECRET:
        logger.warning("Forbidden: secret mismatch")
        return JSONResponse(
            status_code=status.HTTP_403_FORBIDDEN,
            content=ApiError(ok=False, error="Invalid secret").dict(),
        )

    message = "Secret verified. Phase 1 OK. Solver started in background."
    logger.info(message)
    
    # Start the solver
    background_tasks.add_task(solve_quiz, payload.url, payload.email, payload.secret)

    return ApiOK(ok=True, message=message, echo=payload)
