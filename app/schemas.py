from datetime import date, datetime
from typing import Literal

from pydantic import BaseModel


class LLMAction(BaseModel):
    """Structured output schema the LLM must return."""

    operation: Literal[
        "create_candidate",
        "get_candidate",
        "update_candidate",
        "delete_candidate",
    ]
    arguments: dict
    needs_confirmation: bool


class CandidateOut(BaseModel):
    """Serialized candidate for API responses."""

    model_config = {"from_attributes": True}

    id: int
    name: str
    phone: str | None = None
    age: int
    gender: str | None = None
    skills: list[str] | None = None
    desired_occupation: str
    place_of_birth: str
    current_city: str
    current_area: str
    languages: list[str] | None = None
    experience_years: int | None = None
    desired_start_date: date
    desired_salary_min: float | None = None
    desired_salary_max: float | None = None
    verified: bool = False
    profile_complete: bool = False
    created_at: datetime
    updated_at: datetime | None = None


class VoiceCommandResponse(BaseModel):
    """Full response from the /voice-command endpoint."""

    transcript: str
    intent: LLMAction
    result: str
    confirmation_token: str | None = None


class ConfirmationResponse(BaseModel):
    """Response from the /confirm/{token} endpoint."""

    result: str


class ConversationResponse(BaseModel):
    """Response from multi-turn conversation endpoints."""

    session_id: str
    state: str
    transcript: str
    response: str
    slots: dict | None = None
    is_complete: bool
    result: str | None = None


class ChatRequest(BaseModel):
    """Request body for text-based chat endpoint."""

    session_id: str | None = None
    message: str
