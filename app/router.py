"""API router — voice-command pipeline, confirmation, candidates list, health."""

import logging
import tempfile
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Query, UploadFile
from sqlalchemy.orm import Session

from app.confirmation import retrieve_and_remove, store_pending
from app.conversation import handle_turn
from app.crud import OPERATION_MAP
from app.db import get_db
from app.llm import parse_intent
from app.schemas import (
    CandidateOut,
    ChatRequest,
    ConfirmationResponse,
    ConversationResponse,
    LLMAction,
    VoiceCommandResponse,
)
from app.session import get_or_create_session
from app.stt import STTService

logger = logging.getLogger(__name__)

router = APIRouter()

_stt_service: STTService | None = None


def set_stt_service(stt: STTService) -> None:
    """Called at startup to inject the loaded STT model."""
    global _stt_service
    _stt_service = stt


def _get_stt() -> STTService:
    if _stt_service is None:
        raise RuntimeError("STT service not initialised")
    return _stt_service


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_ALLOWED_CONTENT_TYPES = {
    "audio/wav",
    "audio/x-wav",
    "audio/wave",
    "audio/mpeg",
    "audio/mp3",
    "audio/ogg",
    "audio/flac",
    "audio/webm",
}

_AUDIO_EXTENSIONS = {".wav", ".mp3", ".ogg", ".flac", ".webm", ".mpeg"}


def _validate_audio(file: UploadFile) -> None:
    """Raise 400 if the upload doesn't look like an audio file."""
    ct = file.content_type or ""
    if ct in _ALLOWED_CONTENT_TYPES:
        return
    if ct in ("", "application/octet-stream") and file.filename:
        ext = Path(file.filename).suffix.lower()
        if ext in _AUDIO_EXTENSIONS:
            return
    raise HTTPException(
        status_code=400,
        detail=f"Unsupported audio type: {ct}. "
        f"Accepted: {', '.join(sorted(_ALLOWED_CONTENT_TYPES))}",
    )


def _execute_crud(operation: str, arguments: dict, db: Session) -> str:
    """Dispatch a validated operation to the CRUD layer and return a human-readable result."""
    func = OPERATION_MAP.get(operation)
    if func is None:
        raise HTTPException(status_code=400, detail=f"Unknown operation: {operation}")

    try:
        result = func(db, **arguments)
    except TypeError as exc:
        raise HTTPException(
            status_code=400,
            detail=f"Invalid arguments for {operation}: {exc}",
        )
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc))

    if operation == "create_candidate":
        return f"Candidate {result.name} created with id {result.id}"
    if operation == "get_candidate":
        if not result:
            return "No candidates found matching the query"
        names = ", ".join(f"{c.name} (id {c.id})" for c in result)
        return f"Found {len(result)} candidate(s): {names}"
    if operation == "update_candidate":
        return f"Candidate {result.id} updated"
    if operation == "delete_candidate":
        return "Candidate deleted" if result else "Candidate not found"
    return "Done"


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------


@router.post("/voice-command", response_model=VoiceCommandResponse | ConversationResponse)
async def voice_command(
    file: UploadFile,
    session_id: str | None = Query(None),
    db: Session = Depends(get_db),
):
    """Upload audio → transcribe → extract intent → execute or request confirmation.

    If session_id is provided, uses multi-turn conversation mode.
    Otherwise, falls back to single-shot mode for backward compatibility.
    """
    _validate_audio(file)
    stt = _get_stt()

    suffix = Path(file.filename).suffix if file.filename else ".wav"
    try:
        with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as tmp:
            tmp.write(await file.read())
            tmp_path = tmp.name

        transcript = stt.transcribe(tmp_path)
    except Exception as exc:
        logger.exception("STT failed")
        raise HTTPException(status_code=422, detail=f"Audio transcription failed: {exc}")
    finally:
        Path(tmp_path).unlink(missing_ok=True)

    if not transcript:
        raise HTTPException(status_code=422, detail="Could not extract any text from the audio")

    # Multi-turn mode if session_id is provided
    if session_id is not None:
        session = get_or_create_session(session_id)
        try:
            next_state, response = await handle_turn(session, transcript, db)
        except Exception as exc:
            logger.exception("Conversation turn failed")
            raise HTTPException(status_code=500, detail=f"Conversation failed: {exc}")

        return ConversationResponse(
            session_id=session.session_id,
            state=next_state,
            transcript=transcript,
            response=response,
            slots=session.slots if session.slots else None,
            is_complete=(next_state == "done"),
            result=response if next_state == "done" else None,
        )

    # Single-shot mode (backward compatibility)
    try:
        action: LLMAction = await parse_intent(transcript)
    except Exception as exc:
        logger.exception("LLM parse failed")
        raise HTTPException(status_code=502, detail=f"Intent extraction failed: {exc}")

    if action.needs_confirmation:
        token = store_pending(action)
        return VoiceCommandResponse(
            transcript=transcript,
            intent=action,
            result=f"Confirm {action.operation} with args {action.arguments}?",
            confirmation_token=token,
        )

    result_text = _execute_crud(action.operation, action.arguments, db)
    return VoiceCommandResponse(
        transcript=transcript,
        intent=action,
        result=result_text,
    )


@router.post("/confirm/{token}", response_model=ConfirmationResponse)
def confirm_action(token: str, db: Session = Depends(get_db)):
    """Execute a previously stored pending action."""
    action = retrieve_and_remove(token)
    if action is None:
        raise HTTPException(
            status_code=404,
            detail="Confirmation token not found or expired",
        )
    result_text = _execute_crud(action.operation, action.arguments, db)
    return ConfirmationResponse(result=result_text)


@router.post("/chat", response_model=ConversationResponse)
async def chat(request: ChatRequest, db: Session = Depends(get_db)):
    """Text-based conversation endpoint for testing without audio.

    Accepts a text message and optional session_id, processes it through
    the multi-turn conversation state machine, and returns the response.
    """
    session = get_or_create_session(request.session_id)

    try:
        next_state, response = await handle_turn(session, request.message, db)
    except Exception as exc:
        logger.exception("Conversation turn failed")
        raise HTTPException(status_code=500, detail=f"Conversation failed: {exc}")

    return ConversationResponse(
        session_id=session.session_id,
        state=next_state,
        transcript=request.message,
        response=response,
        slots=session.slots if session.slots else None,
        is_complete=(next_state == "done"),
        result=response if next_state == "done" else None,
    )


@router.get("/candidates", response_model=list[CandidateOut])
def list_candidates(db: Session = Depends(get_db)):
    """Return all candidates."""
    from sqlalchemy import select

    from app.models import Candidate

    rows = db.execute(select(Candidate)).scalars().all()
    return list(rows)


@router.get("/health")
def health():
    """Simple liveness check."""
    return {"status": "ok"}
