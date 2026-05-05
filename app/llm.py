"""LLM intent extraction via Ollama structured output."""

from __future__ import annotations

import json
import logging
from datetime import date
from typing import Any

import httpx

from .config import settings
from .llm_prompts import INTENT_CLASSIFICATION_PROMPT, SLOT_EXTRACTION_PROMPT
from .prompts import SYSTEM_PROMPT
from .schemas import LLMAction

logger = logging.getLogger(__name__)

# Module-level client, initialised during app lifespan.
_client: httpx.AsyncClient | None = None


def init_client() -> httpx.AsyncClient:
    """Create and store the shared async HTTP client."""
    global _client
    _client = httpx.AsyncClient(
        base_url=settings.ollama_base_url,
        timeout=httpx.Timeout(120.0, connect=10.0),
    )
    return _client


async def close_client() -> None:
    """Shut down the shared client gracefully."""
    global _client
    if _client is not None:
        await _client.aclose()
        _client = None


def _require_client() -> httpx.AsyncClient:
    if _client is None:
        raise RuntimeError("httpx client not initialised — call init_client() first")
    return _client


# ---------------------------------------------------------------------------
# Single-shot intent parsing (existing, kept for backward compat)
# ---------------------------------------------------------------------------


async def parse_intent(transcript: str) -> LLMAction:
    """Send transcript to Ollama and return a validated LLMAction."""
    client = _require_client()

    payload = {
        "model": settings.ollama_model,
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": transcript},
        ],
        "format": LLMAction.model_json_schema(),
        "stream": False,
    }

    logger.info("Sending transcript to Ollama: %s", transcript)
    response = await client.post("/api/chat", json=payload)
    response.raise_for_status()

    raw_content = response.json()["message"]["content"]
    logger.debug("Raw LLM response: %s", raw_content)

    action = LLMAction.model_validate_json(raw_content)
    logger.info("Parsed intent: %s", action)
    return action


# ---------------------------------------------------------------------------
# Multi-turn: intent classification
# ---------------------------------------------------------------------------

# Schema for structured output — just {"intent": "..."}
_INTENT_SCHEMA = {
    "type": "object",
    "properties": {
        "intent": {
            "type": "string",
            "enum": [
                "register_worker",
                "search_candidate",
                "update_profile",
                "delete_profile",
            ],
        }
    },
    "required": ["intent"],
}


async def classify_intent(transcript: str) -> str:
    """Classify the user's first message into one of the known intents."""
    client = _require_client()

    payload = {
        "model": settings.ollama_model,
        "messages": [
            {"role": "system", "content": INTENT_CLASSIFICATION_PROMPT},
            {"role": "user", "content": transcript},
        ],
        "format": _INTENT_SCHEMA,
        "stream": False,
    }

    logger.info("Classifying intent for: %s", transcript)
    response = await client.post("/api/chat", json=payload)
    response.raise_for_status()

    raw = response.json()["message"]["content"]
    logger.debug("Raw intent classification: %s", raw)

    result = json.loads(raw)
    intent = result["intent"]
    logger.info("Classified intent: %s", intent)
    return intent


# ---------------------------------------------------------------------------
# Multi-turn: slot extraction
# ---------------------------------------------------------------------------


def _strip_markdown_json(raw: str) -> str:
    """Strip markdown code block wrapper from JSON response if present.
    
    Handles various formats:
    - ```json{...}```
    - ```{...}```
    - Plain text followed by ```json{...}```
    - Just {...}
    """
    raw = raw.strip()
    
    # Look for JSON in markdown code blocks
    if "```json" in raw:
        # Extract content between ```json and ```
        start = raw.find("```json") + 7
        end = raw.find("```", start)
        if end != -1:
            raw = raw[start:end].strip()
        else:
            raw = raw[start:].strip()
    elif "```" in raw:
        # Extract content between ``` markers
        start = raw.find("```") + 3
        end = raw.find("```", start)
        if end != -1:
            raw = raw[start:end].strip()
        else:
            raw = raw[start:].strip()
    
    return raw


async def extract_slots(
    history: list[dict[str, str]],
    needed_slots: list[str],
    current_slots: dict[str, Any],
    current_asking: str | None = None,
) -> dict[str, Any]:
    """Extract slot values from the conversation history.

    Returns a dict of newly extracted or corrected slot values (may be empty).
    """
    client = _require_client()

    system_prompt = SLOT_EXTRACTION_PROMPT.format(
        needed_slots=", ".join(needed_slots) if needed_slots else "(none — check for corrections)",
        current_slots=json.dumps(current_slots, default=str) if current_slots else "{}",
        today=date.today().isoformat(),
        current_asking=current_asking or "none",
    )

    # Build messages: system prompt + conversation history
    messages: list[dict[str, str]] = [{"role": "system", "content": system_prompt}]
    messages.extend(history)

    payload = {
        "model": settings.ollama_model,
        "messages": messages,
        "format": "json",  # Force JSON mode - model must return valid JSON
        "stream": False,
    }

    logger.info("Extracting slots, needed: %s", needed_slots)
    response = await client.post("/api/chat", json=payload)
    response.raise_for_status()

    raw = response.json()["message"]["content"]
    logger.debug("Raw slot extraction: %s", raw)

    try:
        extracted = json.loads(raw)
    except json.JSONDecodeError:
        # Fallback: try stripping markdown if direct parse fails
        logger.debug("Direct JSON parse failed, trying markdown strip")
        cleaned = _strip_markdown_json(raw)
        try:
            extracted = json.loads(cleaned)
        except json.JSONDecodeError:
            logger.warning("Slot extraction returned non-JSON: %s", raw)
            return {}

    if not isinstance(extracted, dict):
        logger.warning("Slot extraction returned non-dict: %s", type(extracted))
        return {}

    logger.info("Extracted slots: %s", extracted)
    return extracted
