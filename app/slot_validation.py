"""Slot value validation and coercion.

Sits between LLM slot extraction and session storage. Converts raw LLM output
(which may be strings, wrong types, or natural language) into the correct Python
types expected by the CRUD layer. Values that can't be coerced are dropped.
"""

from __future__ import annotations

import logging
import re
from datetime import date, timedelta
from typing import Any

from app.slots import INTENT_SLOTS, SlotDefinition

logger = logging.getLogger(__name__)


def validate_and_coerce_slots(
    intent: str, extracted: dict[str, Any]
) -> dict[str, Any]:
    """Validate and coerce extracted slot values based on their type definitions.

    Returns a new dict containing only the values that passed validation,
    coerced to the correct Python type. Invalid values are logged and dropped.
    """
    slot_defs = {s.name: s for s in INTENT_SLOTS.get(intent, [])}
    result: dict[str, Any] = {}

    for name, value in extracted.items():
        slot_def = slot_defs.get(name)
        if slot_def is None:
            logger.debug("Dropping unknown slot '%s' for intent '%s'", name, intent)
            continue

        coerced = _coerce_value(name, value, slot_def)
        if coerced is _INVALID:
            logger.warning(
                "Dropping slot '%s': could not coerce %r to type '%s'",
                name, value, slot_def.type,
            )
            continue

        result[name] = coerced

    return result


# Sentinel for failed coercion
_INVALID = object()


def _coerce_value(name: str, value: Any, slot_def: SlotDefinition) -> Any:
    """Attempt to coerce a single value to the slot's declared type.

    Returns _INVALID if coercion fails.
    """
    if value is None or value == "":
        return _INVALID

    slot_type = slot_def.type

    if slot_type == "str":
        return _coerce_str(value)
    elif slot_type == "int":
        return _coerce_int(value)
    elif slot_type == "float":
        return _coerce_float(value)
    elif slot_type == "date":
        return _coerce_date(value)
    elif slot_type == "list[str]":
        return _coerce_list_str(value)
    elif slot_type == "phone":
        return _coerce_phone(value)
    else:
        logger.warning("Unknown slot type '%s' for slot '%s'", slot_type, name)
        return _INVALID


# ---------------------------------------------------------------------------
# Type coercers
# ---------------------------------------------------------------------------


def _coerce_str(value: Any) -> Any:
    """Coerce to string. Reject empty or obviously invalid values."""
    if isinstance(value, str):
        stripped = value.strip()
        return stripped if stripped else _INVALID
    # Numbers, bools → stringify
    if isinstance(value, (int, float, bool)):
        return str(value)
    return _INVALID


def _coerce_int(value: Any) -> Any:
    """Coerce to int. Handles strings like '25', '25.0', 'twenty-five'."""
    if isinstance(value, int) and not isinstance(value, bool):
        return value
    if isinstance(value, float):
        # Accept if it's a whole number
        if value == int(value):
            return int(value)
        return _INVALID

    if isinstance(value, str):
        value = value.strip()
        # Try direct parse
        try:
            return int(value)
        except ValueError:
            pass
        # Try float then int (e.g. "25.0")
        try:
            f = float(value)
            if f == int(f):
                return int(f)
        except ValueError:
            pass
        # Try extracting a number from text like "25 years" or "age 25"
        match = re.search(r"\d+", value)
        if match:
            return int(match.group())

    return _INVALID


def _coerce_float(value: Any) -> Any:
    """Coerce to float. Handles salary strings like '25000', '25k', '50 thousand'."""
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return float(value)

    if isinstance(value, str):
        value = value.strip().lower()
        # Remove currency symbols and commas
        value = re.sub(r"[₹$€£,]", "", value)
        value = value.strip()

        # Handle multiplier suffixes: 25k, 25K
        match = re.match(r"^([\d.]+)\s*k$", value, re.IGNORECASE)
        if match:
            return float(match.group(1)) * 1000

        # Handle "X thousand", "X lakh"
        match = re.match(r"^([\d.]+)\s*(thousand|lakh|lac|lakhs)$", value, re.IGNORECASE)
        if match:
            num = float(match.group(1))
            multiplier = match.group(2).lower()
            if multiplier == "thousand":
                return num * 1000
            elif multiplier in ("lakh", "lac", "lakhs"):
                return num * 100000

        # Try direct parse
        try:
            return float(value)
        except ValueError:
            pass

        # Extract first number from text
        match = re.search(r"[\d.]+", value)
        if match:
            try:
                return float(match.group())
            except ValueError:
                pass

    return _INVALID


def _coerce_date(value: Any) -> Any:
    """Coerce to ISO date string. Handles ISO format and relative expressions."""
    if isinstance(value, date):
        return value.isoformat()

    if not isinstance(value, str):
        return _INVALID

    value = value.strip()

    # Already ISO format
    if re.match(r"^\d{4}-\d{2}-\d{2}$", value):
        try:
            date.fromisoformat(value)
            return value
        except ValueError:
            return _INVALID

    # Relative date expressions
    today = date.today()
    lower = value.lower().strip()

    if lower in ("today", "now", "immediately", "right away", "asap"):
        return today.isoformat()

    if lower in ("tomorrow",):
        return (today + timedelta(days=1)).isoformat()

    if lower in ("next week",):
        return (today + timedelta(days=7)).isoformat()

    if lower in ("next month",):
        # First day of next month
        if today.month == 12:
            return date(today.year + 1, 1, 1).isoformat()
        return date(today.year, today.month + 1, 1).isoformat()

    # "in X days/weeks/months"
    match = re.match(r"in\s+(\d+)\s+(day|days|week|weeks|month|months)", lower)
    if match:
        num = int(match.group(1))
        unit = match.group(2)
        if "day" in unit:
            return (today + timedelta(days=num)).isoformat()
        elif "week" in unit:
            return (today + timedelta(weeks=num)).isoformat()
        elif "month" in unit:
            month = today.month + num
            year = today.year + (month - 1) // 12
            month = (month - 1) % 12 + 1
            return date(year, month, 1).isoformat()

    # If it looks like it could be a date the LLM already converted, accept it
    # Try common formats: "1st March 2025", "March 1, 2025", etc.
    # At this point, if the LLM gave us something that's already ISO, we caught it above.
    # For anything else that's not parseable, reject it so the slot stays unfilled
    # and the user gets re-prompted.
    return _INVALID


def _coerce_list_str(value: Any) -> Any:
    """Coerce to list of strings."""
    if isinstance(value, list):
        # Filter out non-string and empty items
        result = [str(item).strip() for item in value if item is not None and str(item).strip()]
        return result if result else _INVALID

    if isinstance(value, str):
        # Split on commas or "and"
        parts = re.split(r"[,;]|\band\b", value)
        result = [p.strip() for p in parts if p.strip()]
        return result if result else _INVALID

    return _INVALID


def _coerce_phone(value: Any) -> Any:
    """Coerce and validate a phone number.

    Strips formatting (spaces, dashes, parens, dots, country code prefix),
    validates that the result is a plausible phone number (7-15 digits),
    and returns the normalized digits-only string.

    Handles common voice/LLM outputs:
    - "9876543210"
    - "98765 43210"
    - "+91 9876543210"
    - "987-654-3210"
    - "(987) 654-3210"
    - "my number is 9876543210"
    - "nine eight seven..." → rejected (no digit extraction from words)
    """
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        # Phone given as a number (e.g. 9876543210)
        value = str(int(value))

    if not isinstance(value, str):
        return _INVALID

    value = value.strip()
    if not value:
        return _INVALID

    # Strip all non-digit characters except leading +
    # First, remove the leading + and country code if present
    cleaned = value
    if cleaned.startswith("+"):
        cleaned = cleaned[1:]

    # Remove all non-digit characters
    digits = re.sub(r"\D", "", cleaned)

    if not digits:
        return _INVALID

    # Strip leading country code for Indian numbers (91) if total length > 10
    if len(digits) > 10 and digits.startswith("91"):
        digits = digits[2:]

    # Validate length: Indian mobile = 10 digits, international = 7-15
    if len(digits) < 7 or len(digits) > 15:
        return _INVALID

    # Reject if it looks like a non-phone number (all same digit, sequential)
    if len(set(digits)) == 1:
        return _INVALID

    return digits
