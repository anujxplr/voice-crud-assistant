"""In-memory conversation session store with TTL."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any
from uuid import uuid4

from app.config import settings

# ---------------------------------------------------------------------------
# Session dataclass
# ---------------------------------------------------------------------------


@dataclass
class ConversationSession:
    session_id: str
    state: str = "greeting"
    intent: str | None = None
    slots: dict[str, Any] = field(default_factory=dict)
    history: list[dict[str, str]] = field(default_factory=list)
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

    def touch(self) -> None:
        """Update the last-activity timestamp."""
        self.updated_at = datetime.now(timezone.utc)

    def add_user_message(self, text: str) -> None:
        self.history.append({"role": "user", "content": text})
        self.touch()

    def add_assistant_message(self, text: str) -> None:
        self.history.append({"role": "assistant", "content": text})
        self.touch()

    @property
    def is_expired(self) -> bool:
        elapsed = (datetime.now(timezone.utc) - self.updated_at).total_seconds()
        return elapsed > settings.session_ttl_seconds


# ---------------------------------------------------------------------------
# Store
# ---------------------------------------------------------------------------

_sessions: dict[str, ConversationSession] = {}


def _cleanup_expired() -> None:
    """Remove sessions that have exceeded the TTL."""
    expired = [sid for sid, s in _sessions.items() if s.is_expired]
    for sid in expired:
        del _sessions[sid]


def get_or_create_session(session_id: str | None = None) -> ConversationSession:
    """Return an existing session or create a new one.

    If *session_id* is ``None``, a new UUID-based session is created.
    If a session_id is provided but has expired, a fresh session is created
    with the same id.
    """
    _cleanup_expired()

    if session_id is None:
        session_id = uuid4().hex

    session = _sessions.get(session_id)
    if session is None:
        session = ConversationSession(session_id=session_id)
        _sessions[session_id] = session

    return session


def get_session(session_id: str) -> ConversationSession | None:
    """Return the session if it exists and hasn't expired, else ``None``."""
    _cleanup_expired()
    return _sessions.get(session_id)


def delete_session(session_id: str) -> bool:
    """Remove a session. Returns ``True`` if it existed."""
    return _sessions.pop(session_id, None) is not None
