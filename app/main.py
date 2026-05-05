"""FastAPI application entrypoint with lifespan management."""

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.db import init_db
from app.llm import close_client, init_client
from app.router import router, set_stt_service
from app.stt import STTService

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s [%(name)s] %(message)s",
)
logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Startup: init DB, load Whisper model, create httpx client.
    Shutdown: close httpx client."""
    logger.info("Initialising database")
    init_db()

    logger.info("Loading Whisper STT model")
    stt = STTService()
    set_stt_service(stt)

    logger.info("Creating Ollama HTTP client")
    init_client()

    yield

    logger.info("Shutting down Ollama HTTP client")
    await close_client()


app = FastAPI(
    title="Voice CRUD Assistant",
    description="Voice-to-database pipeline: speak a command, get a CRUD result.",
    version="0.1.0",
    lifespan=lifespan,
)

app.include_router(router)
