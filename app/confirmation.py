"""In-memory pending action store with TTL for destructive operations."""

from dataclasses import dataclass, field
from datetime import datetime, timezone
from uuid import uuid4

from app.config import settings
from app.schemas import LLMAction


@dataclass
class PendingAction:
    action: LLMAction
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    token: str = field(default_factory=lambda: uuid4().hex)


# Module-level store — shared across the process lifetime.
pending_actions: dict[str, PendingAction] = {}


def _cleanup_expired() -> None:
    """Remove entries older than the configured TTL."""
    now = datetime.now(timezone.utc)
    expired = [
        tok
        for tok, pa in pending_actions.items()
        if (now - pa.created_at).total_seconds() > settings.confirmation_ttl_seconds
    ]
    for tok in expired:
        del pending_actions[tok]


def store_pending(action: LLMAction) -> str:
    """Store a pending action and return its UUID token."""
    _cleanup_expired()
    pa = PendingAction(action=action)
    pending_actions[pa.token] = pa
    return pa.token


def retrieve_and_remove(token: str) -> LLMAction | None:
    """Return the action if the token is valid and not expired, else None."""
    _cleanup_expired()
    pa = pending_actions.pop(token, None)
    if pa is None:
        return None
    return pa.action
