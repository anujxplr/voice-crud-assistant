"""Multi-turn conversation state machine."""

from __future__ import annotations

import logging
import re
from typing import Any

from sqlalchemy.orm import Session

from app.crud import OPERATION_MAP, get_candidate
from app.llm import classify_intent, extract_slots
from app.session import ConversationSession
from app.slot_validation import validate_and_coerce_slots
from app.slots import INTENT_SLOTS, get_missing_required_slots, get_next_prompt, get_slot_names

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------


async def handle_turn(
    session: ConversationSession, transcript: str, db: Session
) -> tuple[str, str]:
    """Process one turn of conversation.

    Returns (next_state, response_text).
    """
    # Add user message to history
    session.add_user_message(transcript)

    # Dispatch to the appropriate state handler
    handler = _STATE_HANDLERS.get(session.state)
    if handler is None:
        logger.error("Unknown state: %s", session.state)
        return "greeting", "Sorry, something went wrong. Let's start over. How can I help you?"

    next_state, response = await handler(session, transcript, db)
    session.state = next_state

    # Add assistant response to history
    session.add_assistant_message(response)

    return next_state, response


# ---------------------------------------------------------------------------
# State handlers
# ---------------------------------------------------------------------------


async def handle_greeting(
    session: ConversationSession, transcript: str, db: Session
) -> tuple[str, str]:
    """Welcome the user and move to intent identification."""
    # The greeting state is just a pass-through — we immediately try to identify intent
    return await handle_identify_intent(session, transcript, db)


async def handle_identify_intent(
    session: ConversationSession, transcript: str, db: Session
) -> tuple[str, str]:
    """Classify the user's intent and transition to the appropriate next state."""
    try:
        intent = await classify_intent(transcript)
        session.intent = intent
        logger.info("Identified intent: %s", intent)
    except Exception as e:
        logger.exception("Intent classification failed")
        return (
            "greeting",
            "Sorry, I didn't understand that. Could you tell me what you'd like to do? "
            "For example, 'I want to register' or 'Find me a plumber'.",
        )

    # For search and delete, we can often execute immediately if the user provided enough info
    # For create and update, we need to collect slots
    if intent == "register_worker":
        # Start slot filling
        return await handle_slot_filling(session, transcript, db)
    elif intent == "update_profile":
        # Update needs to resolve which candidate first
        return await handle_resolve_candidate(session, transcript, db)
    elif intent == "search_candidate":
        # Search can work with partial info, so we try extraction then execute
        return await handle_slot_filling(session, transcript, db)
    elif intent == "delete_profile":
        # Delete needs to resolve which candidate first (same as update)
        return await handle_resolve_candidate(session, transcript, db)
    else:
        return "greeting", "I'm not sure what you want to do. Could you try again?"


async def handle_slot_filling(
    session: ConversationSession, transcript: str, db: Session
) -> tuple[str, str]:
    """Extract slots from the conversation and ask for missing required ones."""
    if session.intent is None:
        logger.error("slot_filling state reached without intent")
        return "greeting", "Sorry, something went wrong. Let's start over."

    # Determine which slots are still missing so the LLM focuses on those
    missing_before = get_missing_required_slots(session.intent, session.slots)
    all_slot_names = get_slot_names(session.intent)

    # For update/delete: once candidate is resolved (id is set), skip identification slots
    _IDENTIFICATION_SLOTS = {"candidate_identifier", "candidate_phone"}
    if session.intent in ("update_profile", "delete_profile") and "id" in session.slots:
        missing_before = [s for s in missing_before if s not in _IDENTIFICATION_SLOTS]
        all_slot_names = [s for s in all_slot_names if s not in _IDENTIFICATION_SLOTS]

    # Tell the LLM about missing slots + any optional slots not yet filled
    unfilled_slots = [s for s in all_slot_names if s not in session.slots]

    # Figure out what question we last asked so the LLM can map short answers
    last_asked_slot = missing_before[0] if missing_before else None

    # For optional-only intents (e.g. search), determine which optional slot
    # we're currently asking about so the LLM can map short answers.
    if last_asked_slot is None:
        all_defs = _get_slot_definitions(session.intent)
        for slot_def in all_defs:
            if slot_def.name not in session.slots:
                last_asked_slot = slot_def.name
                break

    # Extract slots from the full conversation history (including current user message)
    try:
        extracted = await extract_slots(
            history=session.history,
            needed_slots=unfilled_slots,
            current_slots=session.slots,
            current_asking=last_asked_slot,
        )
        # Filter out None/empty values to avoid overwriting good data
        filtered = {
            k: v for k, v in extracted.items()
            if v is not None and v != "" and v != [] and k in all_slot_names
        }
        # Validate and coerce types before merging
        validated = validate_and_coerce_slots(session.intent, filtered)
        # Merge validated slots into session
        session.slots.update(validated)
        logger.info("Current slots after extraction: %s", session.slots)
    except Exception as e:
        logger.exception("Slot extraction failed")
        # Continue anyway — we'll ask for missing slots

    # Check if all required slots are filled
    missing = get_missing_required_slots(session.intent, session.slots)

    if missing:
        # Ask for the next missing slot
        prompt = get_next_prompt(session.intent, session.slots)
        if prompt is None:
            # Shouldn't happen, but fallback
            prompt = f"I still need: {', '.join(missing)}. Could you provide those?"
        return "slot_filling", prompt
    else:
        # For intents where all slots are optional (e.g. search), give the user
        # a chance to provide at least some info before jumping to confirmation.
        if session.intent in ("search_candidate",):
            # Check if user is signaling they're done providing info
            transcript_lower = transcript.lower().strip()
            done_signals = ["yes", "yeah", "yep", "sure", "proceed", "search", "that's it",
                            "that's all", "go ahead", "find", "do it"]
            if session.slots and any(sig in transcript_lower for sig in done_signals):
                # For search, skip confirmation and execute directly
                return await handle_execute(session, transcript, db)

            # Ask the next unfilled optional slot
            all_defs = _get_slot_definitions(session.intent)
            for slot_def in all_defs:
                if slot_def.name not in session.slots:
                    return "slot_filling", slot_def.prompt

        # For update_profile, don't iterate through optional slots — just ask
        # the user what they want to change and confirm when they signal done.
        if session.intent == "update_profile":
            # We need at least one field besides "id" to update
            update_fields = {k: v for k, v in session.slots.items() if k not in ("id", "candidate_identifier")}
            transcript_lower = transcript.lower().strip()
            done_signals = ["yes", "yeah", "yep", "sure", "proceed", "that's it",
                            "that's all", "go ahead", "do it", "done", "update"]
            if update_fields and any(sig in transcript_lower for sig in done_signals):
                return await handle_confirm(session, transcript, db)
            if update_fields:
                # We have some fields — ask if they want to change anything else
                return (
                    "slot_filling",
                    "Got it. Anything else you'd like to change, or should I go ahead with the update?",
                )
            else:
                # No fields collected yet — ask what to change
                return (
                    "slot_filling",
                    "What would you like to update? You can change name, age, occupation, "
                    "city, area, salary, phone, skills, etc.",
                )

        # All required slots filled (and no more optional to ask) → confirm
        return await handle_confirm(session, transcript, db)


async def handle_resolve_candidate(
    session: ConversationSession, transcript: str, db: Session
) -> tuple[str, str]:
    """Resolve which candidate the user wants to update using name + phone.

    Flow:
    1. Collect name from user
    2. Collect phone number for identity confirmation
    3. Look up by name, then verify phone matches
    4. If multiple name matches, present options; confirm with phone
    """
    if session.intent is None:
        return "greeting", "Sorry, something went wrong. Let's start over."

    # Check if we already presented options and the user is selecting one
    pending_options = session.slots.get("_pending_candidates")
    if pending_options:
        return _handle_option_selection(session, transcript, pending_options)

    # Determine which identification slots we still need
    has_name = "candidate_identifier" in session.slots
    has_phone = "candidate_phone" in session.slots

    # Figure out what we're currently asking for
    if not has_name:
        current_asking = "candidate_identifier"
    elif not has_phone:
        current_asking = "candidate_phone"
    else:
        current_asking = None

    # Extract identification slots from the user's message
    needed = []
    if not has_name:
        needed.append("candidate_identifier")
    if not has_phone:
        needed.append("candidate_phone")

    if needed:
        try:
            extracted = await extract_slots(
                history=session.history,
                needed_slots=needed,
                current_slots=session.slots,
                current_asking=current_asking,
            )
            filtered = {
                k: v for k, v in extracted.items()
                if v is not None and v != "" and k in ("candidate_identifier", "candidate_phone")
            }
            validated = validate_and_coerce_slots(session.intent, filtered)
            session.slots.update(validated)
        except Exception:
            logger.exception("Slot extraction failed during resolve_candidate")

    # Re-check what we have
    has_name = "candidate_identifier" in session.slots
    has_phone = "candidate_phone" in session.slots

    if not has_name:
        return (
            "resolve_candidate",
            "Which candidate do you want to update? Tell me their name.",
        )

    if not has_phone:
        return (
            "resolve_candidate",
            "And what's their phone number? I'll use it to confirm their identity.",
        )

    # We have both name and phone — do the lookup
    identifier = session.slots.pop("candidate_identifier")
    phone = session.slots.pop("candidate_phone")

    matches = get_candidate(db, name=str(identifier))

    if not matches:
        return (
            "resolve_candidate",
            f"I couldn't find anyone named '{identifier}'. "
            "Could you try again with their full name?",
        )

    # Filter matches by phone number
    phone_matches = [c for c in matches if c.phone and _normalize_phone(c.phone) == _normalize_phone(phone)]

    if len(phone_matches) == 1:
        # Perfect match — name + phone confirmed identity
        candidate = phone_matches[0]
        session.slots["id"] = candidate.id

        if session.intent == "delete_profile":
            return (
                "confirm",
                f"Verified {candidate.name} (phone ending in ...{_normalize_phone(phone)[-4:]}). "
                f"Are you sure you want to delete this profile? This cannot be undone.",
            )
        return (
            "slot_filling",
            f"Verified! Updating {candidate.name} (phone ending in ...{_normalize_phone(phone)[-4:]}). "
            "What would you like to change?",
        )
    elif len(phone_matches) > 1:
        # Rare: multiple people with same name AND phone — present options
        options_data = [
            {"id": c.id, "name": c.name,
             "occupation": c.desired_occupation, "city": c.current_city}
            for c in phone_matches[:5]
        ]
        session.slots["_pending_candidates"] = options_data
        options_text = "\n".join(
            f"  {i}. {opt['name']} — {opt['occupation']}, {opt['city']}"
            for i, opt in enumerate(options_data, 1)
        )
        return (
            "resolve_candidate",
            f"I found multiple matches:\n{options_text}\n\n"
            "Which one? Just say the number (1, 2, 3...).",
        )
    else:
        # Name matched but phone didn't — could be wrong phone or wrong person
        if len(matches) == 1:
            return (
                "resolve_candidate",
                f"I found {matches[0].name}, but the phone number doesn't match our records. "
                "Could you double-check and tell me the name and phone number again?",
            )
        else:
            # Multiple name matches, none with matching phone
            return (
                "resolve_candidate",
                f"I found {len(matches)} people named '{identifier}', but none match "
                "that phone number. Could you try again with the correct name and phone?",
            )


def _normalize_phone(phone: str) -> str:
    """Strip non-digit characters for phone comparison."""
    return re.sub(r"\D", "", phone)


def _handle_option_selection(
    session: ConversationSession, transcript: str, pending_options: list[dict]
) -> tuple[str, str]:
    """Handle user's response to presented candidate options (numbered list)."""
    transcript_lower = transcript.lower().strip()

    # Extract a number from the response
    match = re.search(r"\d+", transcript_lower)
    if match:
        choice = int(match.group())
        if 1 <= choice <= len(pending_options):
            candidate = pending_options[choice - 1]
            session.slots.pop("_pending_candidates", None)
            session.slots["id"] = candidate["id"]

            if session.intent == "delete_profile":
                return (
                    "confirm",
                    f"You want to delete {candidate['name']} ({candidate['occupation']}, "
                    f"{candidate['city']}). Are you sure? This cannot be undone.",
                )
            return (
                "slot_filling",
                f"Got it, updating {candidate['name']}. "
                "What would you like to change?",
            )
        else:
            options_text = "\n".join(
                f"  {i}. {opt['name']} — {opt['occupation']}, {opt['city']}"
                for i, opt in enumerate(pending_options, 1)
            )
            return (
                "resolve_candidate",
                f"That number isn't in the list. Please pick one:\n{options_text}",
            )

    # Check if user said a name that matches one of the options
    for opt in pending_options:
        if opt["name"].lower() in transcript_lower:
            session.slots.pop("_pending_candidates", None)
            session.slots["id"] = opt["id"]

            if session.intent == "delete_profile":
                return (
                    "confirm",
                    f"You want to delete {opt['name']} ({opt['occupation']}, "
                    f"{opt['city']}). Are you sure? This cannot be undone.",
                )
            return (
                "slot_filling",
                f"Got it, updating {opt['name']}. "
                "What would you like to change?",
            )

    options_text = "\n".join(
        f"  {i}. {opt['name']} — {opt['occupation']}, {opt['city']}"
        for i, opt in enumerate(pending_options, 1)
    )
    return (
        "resolve_candidate",
        f"I didn't catch which one. Please pick a number:\n{options_text}",
    )


async def handle_confirm(
    session: ConversationSession, transcript: str, db: Session
) -> tuple[str, str]:
    """Read back collected data and ask for confirmation."""
    if session.intent is None:
        return "greeting", "Sorry, something went wrong. Let's start over."

    # Check if the last assistant message was already a confirmation prompt
    confirmation_phrases = ["Is that correct?", "Should I proceed?", "Are you sure?"]
    last_assistant_msgs = [m for m in session.history if m["role"] == "assistant"]
    already_asked_confirmation = last_assistant_msgs and any(
        phrase in last_assistant_msgs[-1]["content"] for phrase in confirmation_phrases
    )

    if already_asked_confirmation:
        # User is responding to confirmation
        transcript_lower = transcript.lower().strip()
        if any(word in transcript_lower for word in ["yes", "correct", "right", "yep", "yeah", "sure", "proceed"]):
            # Confirmed → execute
            return await handle_execute(session, transcript, db)
        elif any(
            word in transcript_lower for word in ["no", "wrong", "change", "fix", "update"]
        ):
            # User wants to correct something → back to slot filling
            return (
                "slot_filling",
                "Okay, what would you like to change? Just tell me the correct information.",
            )
        else:
            # The user might be providing additional slot info instead of confirming.
            # Try to extract slots from their message.
            all_slot_names = get_slot_names(session.intent)
            unfilled = [s for s in all_slot_names if s not in session.slots]
            if unfilled:
                try:
                    extracted = await extract_slots(
                        history=session.history,
                        needed_slots=unfilled,
                        current_slots=session.slots,
                        current_asking=None,
                    )
                    filtered = {
                        k: v for k, v in extracted.items()
                        if v is not None and v != "" and v != [] and k in all_slot_names
                    }
                    # Validate and coerce types before merging
                    validated = validate_and_coerce_slots(session.intent, filtered)
                    if validated:
                        session.slots.update(validated)
                        # Re-show confirmation with updated data
                        confirmation = _build_confirmation_message(session.intent, session.slots)
                        return "confirm", confirmation
                except Exception:
                    pass

            return "confirm", "Sorry, I didn't catch that. Is the information correct? (yes or no)"

    # Generate confirmation message
    confirmation = _build_confirmation_message(session.intent, session.slots)
    return "confirm", confirmation


async def handle_execute(
    session: ConversationSession, transcript: str, db: Session
) -> tuple[str, str]:
    """Execute the CRUD operation with collected slots."""
    if session.intent is None:
        return "greeting", "Sorry, something went wrong."

    # Map intent to CRUD operation
    operation_name = _intent_to_operation(session.intent)
    operation = OPERATION_MAP.get(operation_name)

    if operation is None:
        logger.error("No CRUD operation for intent: %s", session.intent)
        return "done", "Sorry, I couldn't complete that action."

    try:
        # Prepare slots for CRUD — remove internal-only keys
        crud_slots = {k: v for k, v in session.slots.items() if k not in ("candidate_identifier", "candidate_phone", "_pending_candidates")}

        # Call the CRUD function
        result = operation(db, **crud_slots)
        logger.info("CRUD operation %s succeeded: %s", operation_name, result)

        # Format success message
        response = _format_success_message(session.intent, result)
        return "done", response

    except Exception as e:
        logger.exception("CRUD operation %s failed", operation_name)
        return "done", f"Sorry, something went wrong: {str(e)}"


async def handle_done(
    session: ConversationSession, transcript: str, db: Session
) -> tuple[str, str]:
    """Conversation complete. Ask if user wants to do something else."""
    transcript_lower = transcript.lower()

    # Check if user wants to do something else
    if any(word in transcript_lower for word in ["yes", "another", "more", "also"]):
        # Reset session for new conversation
        session.intent = None
        session.slots = {}
        return "greeting", "Sure! What would you like to do?"
    else:
        # End conversation
        return "done", "Okay, goodbye! Feel free to come back anytime."


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _intent_to_operation(intent: str) -> str:
    """Map intent name to CRUD operation name."""
    mapping = {
        "register_worker": "create_candidate",
        "search_candidate": "get_candidate",
        "update_profile": "update_candidate",
        "delete_profile": "delete_candidate",
    }
    return mapping.get(intent, "")


def _get_slot_definitions(intent: str) -> list:
    """Return the list of SlotDefinition objects for the given intent."""
    return INTENT_SLOTS.get(intent, [])


def _build_confirmation_message(intent: str, slots: dict[str, Any]) -> str:
    """Generate a human-readable confirmation message."""
    if intent == "register_worker":
        parts = []
        if "name" in slots:
            parts.append(f"Name: {slots['name']}")
        if "age" in slots:
            parts.append(f"Age: {slots['age']}")
        if "desired_occupation" in slots:
            parts.append(f"Occupation: {slots['desired_occupation']}")
        if "current_city" in slots and "current_area" in slots:
            parts.append(f"Location: {slots['current_area']}, {slots['current_city']}")
        if "place_of_birth" in slots:
            parts.append(f"Born in: {slots['place_of_birth']}")
        if "desired_start_date" in slots:
            parts.append(f"Available from: {slots['desired_start_date']}")
        if "desired_salary_min" in slots:
            parts.append(f"Minimum salary: {slots['desired_salary_min']}")
        if "phone" in slots:
            parts.append(f"Phone: {slots['phone']}")
        if "gender" in slots:
            parts.append(f"Gender: {slots['gender']}")
        if "skills" in slots:
            parts.append(f"Skills: {', '.join(slots['skills'])}")
        if "languages" in slots:
            parts.append(f"Languages: {', '.join(slots['languages'])}")
        if "experience_years" in slots:
            parts.append(f"Experience: {slots['experience_years']} years")

        summary = "\n".join(parts)
        return f"Here's what I have:\n{summary}\n\nIs that correct?"

    elif intent == "update_profile":
        candidate_id = slots.get("id", "unknown")
        parts = [f"{k}: {v}" for k, v in slots.items() if k not in ("id", "candidate_identifier", "candidate_phone", "_pending_candidates")]
        summary = "\n".join(parts)
        return f"I'll update candidate ID {candidate_id} with:\n{summary}\n\nIs that correct?"

    elif intent == "search_candidate":
        parts = [f"{k}: {v}" for k, v in slots.items()]
        summary = "\n".join(parts) if parts else "any criteria"
        return f"Searching for candidates with:\n{summary}\n\nShould I proceed?"

    elif intent == "delete_profile":
        candidate_id = slots.get("id", "unknown")
        return f"You want to delete candidate ID {candidate_id}. Are you sure? This cannot be undone."

    return "Is that correct?"


def _format_success_message(intent: str, result: Any) -> str:
    """Format the success message based on intent and result."""
    if intent == "register_worker":
        # result is a Candidate object
        return (
            f"Done! {result.name} has been registered with ID {result.id}. "
            "Anything else I can help with?"
        )

    elif intent == "update_profile":
        return f"Profile updated successfully! Anything else?"

    elif intent == "search_candidate":
        # result is a list of Candidates
        if not result:
            return "No candidates found matching those criteria. Want to try different filters?"
        elif len(result) == 1:
            c = result[0]
            return (
                f"Found 1 candidate: {c.name}, {c.age}, {c.desired_occupation}, "
                f"{c.current_city}. ID: {c.id}. Need anything else?"
            )
        else:
            summary = "\n".join(
                [
                    f"- {c.name}, {c.age}, {c.desired_occupation}, {c.current_city} (ID: {c.id})"
                    for c in result[:5]
                ]
            )
            more = f"\n...and {len(result) - 5} more." if len(result) > 5 else ""
            return f"Found {len(result)} candidates:\n{summary}{more}\n\nAnything else?"

    elif intent == "delete_profile":
        # result is a boolean
        if result:
            return "Candidate deleted successfully. Anything else?"
        else:
            return "Candidate not found. Nothing to delete. Anything else?"

    return "Done! Anything else?"


# ---------------------------------------------------------------------------
# State handler registry
# ---------------------------------------------------------------------------

_STATE_HANDLERS = {
    "greeting": handle_greeting,
    "identify_intent": handle_identify_intent,
    "slot_filling": handle_slot_filling,
    "resolve_candidate": handle_resolve_candidate,
    "confirm": handle_confirm,
    "execute": handle_execute,
    "done": handle_done,
}
