"""Slot definitions for multi-turn conversation intents."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class SlotDefinition:
    name: str
    type: str  # "str", "int", "float", "date", "list[str]"
    required: bool
    prompt: str
    retry_prompt: str


# ---------------------------------------------------------------------------
# Slot sets per intent
# ---------------------------------------------------------------------------

REGISTER_WORKER_SLOTS: list[SlotDefinition] = [
    SlotDefinition(
        name="name",
        type="str",
        required=True,
        prompt="What's your name?",
        retry_prompt="Sorry, I didn't catch your name. Could you say it again?",
    ),
    SlotDefinition(
        name="age",
        type="int",
        required=True,
        prompt="How old are you?",
        retry_prompt="I need your age as a number. How old are you?",
    ),
    SlotDefinition(
        name="desired_occupation",
        type="str",
        required=True,
        prompt="What kind of work are you looking for?",
        retry_prompt="What job or trade are you interested in?",
    ),
    SlotDefinition(
        name="current_city",
        type="str",
        required=True,
        prompt="Which city do you live in?",
        retry_prompt="Which city are you currently in?",
    ),
    SlotDefinition(
        name="current_area",
        type="str",
        required=True,
        prompt="Which area or neighborhood?",
        retry_prompt="What's your area or locality within the city?",
    ),
    SlotDefinition(
        name="place_of_birth",
        type="str",
        required=True,
        prompt="Where were you born?",
        retry_prompt="Which city or town were you born in?",
    ),
    SlotDefinition(
        name="desired_start_date",
        type="date",
        required=True,
        prompt="When can you start working?",
        retry_prompt="When would you be available to start? A date or something like 'next month' works.",
    ),
    SlotDefinition(
        name="desired_salary_min",
        type="float",
        required=True,
        prompt="What's the minimum salary you'd accept?",
        retry_prompt="What's the lowest monthly salary you'd be okay with? Just the number is fine.",
    ),
    SlotDefinition(
        name="phone",
        type="phone",
        required=True,
        prompt="What's your phone number?",
        retry_prompt="Could you repeat your phone number?",
    ),
    SlotDefinition(
        name="gender",
        type="str",
        required=False,
        prompt="Are you male, female, or other?",
        retry_prompt="Could you tell me your gender?",
    ),
    SlotDefinition(
        name="skills",
        type="list[str]",
        required=False,
        prompt="What other skills or trades do you have?",
        retry_prompt="Any additional skills besides your main occupation?",
    ),
    SlotDefinition(
        name="languages",
        type="list[str]",
        required=False,
        prompt="What languages do you speak?",
        retry_prompt="Which languages can you speak?",
    ),
    SlotDefinition(
        name="experience_years",
        type="int",
        required=False,
        prompt="How many years of experience do you have?",
        retry_prompt="How many years have you been working?",
    ),
    SlotDefinition(
        name="desired_salary_max",
        type="float",
        required=False,
        prompt="And what's the maximum salary you'd expect?",
        retry_prompt="What's the most you'd expect to earn monthly?",
    ),
]

# For update, we first need to identify the candidate, then collect fields to change.
# The "candidate_identifier" slot is used during resolution (name input).
# The "candidate_phone" slot is used for phone-based confirmation.
# The "id" slot holds the resolved candidate ID after lookup.
# All other fields are optional — we only update what the user mentions.
UPDATE_PROFILE_SLOTS: list[SlotDefinition] = [
    SlotDefinition(
        name="candidate_identifier",
        type="str",
        required=True,
        prompt="Which candidate do you want to update? Tell me their name.",
        retry_prompt="I need to know who to update. What's their name?",
    ),
    SlotDefinition(
        name="candidate_phone",
        type="phone",
        required=True,
        prompt="And what's their phone number? I'll use it to confirm their identity.",
        retry_prompt="I need the phone number to verify. Could you tell me again?",
    ),
    *[
        SlotDefinition(
            name=s.name, type=s.type, required=False, prompt=s.prompt, retry_prompt=s.retry_prompt
        )
        for s in REGISTER_WORKER_SLOTS
        if s.name not in ("phone",)  # phone is used for identification, not as an update field here
    ],
]

# Search: recruiter describes what they're looking for. All optional.
SEARCH_CANDIDATE_SLOTS: list[SlotDefinition] = [
    SlotDefinition(
        name="desired_occupation",
        type="str",
        required=False,
        prompt="What occupation or trade are you looking for?",
        retry_prompt="What kind of worker do you need?",
    ),
    SlotDefinition(
        name="current_city",
        type="str",
        required=False,
        prompt="In which city?",
        retry_prompt="Which city should the candidates be in?",
    ),
    SlotDefinition(
        name="desired_salary_max",
        type="float",
        required=False,
        prompt="What's your budget for salary?",
        retry_prompt="What's the maximum salary you can offer?",
    ),
    SlotDefinition(
        name="experience_years",
        type="int",
        required=False,
        prompt="Minimum years of experience needed?",
        retry_prompt="How many years of experience should they have at least?",
    ),
]

# Delete uses the same name+phone resolution pattern for security.
DELETE_PROFILE_SLOTS: list[SlotDefinition] = [
    SlotDefinition(
        name="candidate_identifier",
        type="str",
        required=True,
        prompt="Which candidate do you want to delete? Tell me their name.",
        retry_prompt="I need to know who to delete. What's their name?",
    ),
    SlotDefinition(
        name="candidate_phone",
        type="phone",
        required=True,
        prompt="And what's their phone number? I need it to confirm their identity before deleting.",
        retry_prompt="I need the phone number to verify before deletion. Could you tell me again?",
    ),
]

# ---------------------------------------------------------------------------
# Intent → slot set mapping
# ---------------------------------------------------------------------------

INTENT_SLOTS: dict[str, list[SlotDefinition]] = {
    "register_worker": REGISTER_WORKER_SLOTS,
    "update_profile": UPDATE_PROFILE_SLOTS,
    "search_candidate": SEARCH_CANDIDATE_SLOTS,
    "delete_profile": DELETE_PROFILE_SLOTS,
}

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _slot_map(intent: str) -> dict[str, SlotDefinition]:
    """Return {name: SlotDefinition} for the given intent."""
    return {s.name: s for s in INTENT_SLOTS.get(intent, [])}


def get_missing_required_slots(intent: str, current_slots: dict[str, Any]) -> list[str]:
    """Return names of required slots that haven't been filled yet."""
    slots = INTENT_SLOTS.get(intent, [])
    return [s.name for s in slots if s.required and s.name not in current_slots]


def get_next_prompt(intent: str, current_slots: dict[str, Any]) -> str | None:
    """Return the prompt for the next missing required slot, or None if all filled."""
    sm = _slot_map(intent)
    for name in get_missing_required_slots(intent, current_slots):
        slot = sm[name]
        return slot.prompt
    return None


def get_slot_names(intent: str) -> list[str]:
    """Return all slot names (required + optional) for an intent."""
    return [s.name for s in INTENT_SLOTS.get(intent, [])]
