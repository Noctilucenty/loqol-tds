"""Why a given question is routed to voice or to tap.

The routing decision is the substance of this exercise, so the reasons live in
data rather than in prose: every question carries a `lane` plus the key of the
rationale behind it, and the README's routing table is generated from here.

The organising thesis, in one line:

    Speak when the bottleneck is understanding.
    Tap when the bottleneck is enumeration.

That is not the intuitive split. The intuitive split is "voice for the scary
legal parts, tap for the easy parts", which produces a seller reading fifty
appliance names aloud. The bottleneck framing predicts the opposite and is the
one that survives contact with the actual form.
"""

from enum import StrEnum


class Lane(StrEnum):
    TAP = "tap"
    VOICE = "voice"


class Why(StrEnum):
    ENUMERATION = "enumeration"
    PRECISION = "precision"
    GATE = "gate"
    COMPREHENSION = "comprehension"
    NARRATIVE = "narrative"
    COMPOUND = "compound"
    AGENT_OWNED = "agent_owned"


RATIONALE: dict[Why, str] = {
    Why.ENUMERATION: (
        "Fifty checkboxes. Range, oven, gazebo, sauna. None of them is hard to "
        "understand - the work is just remembering what your own house has, and "
        "there are a lot of them. Reading fifty items out loud takes longer than "
        "tapping them, you lose track of where you are, and you cannot glance "
        "back to see the one you skipped. So: a grid."
    ),
    Why.PRECISION: (
        "An address, a room name, a count. Speech-to-text is at its worst on "
        "exactly this sort of thing, and an address typo ends up printed on all "
        "three pages of a legal document. Not worth the risk to save a few "
        "seconds."
    ),
    Why.GATE: (
        "One yes or no that opens or closes everything after it. Say no to the "
        "pool and three questions disappear. That is easier to follow when you "
        "can see it happen than when someone describes it to you."
    ),
    Why.COMPREHENSION: (
        "\"Any encroachments, easements or similar matters that may affect your "
        "interest in the subject property.\" Nobody who owns a house can answer "
        "that as written. The problem is not typing, it is that the question "
        "makes no sense until someone rephrases it and gives you an example - "
        "which is a conversation, not a form field."
    ),
    Why.NARRATIVE: (
        "Yes on its own is useless here. The form wants what happened, roughly "
        "when, and whether anyone fixed it. That is a paragraph, and asking "
        "someone to thumb-type a paragraph about their roof at ten at night is "
        "how you get an empty box. Talking is easier, and the assistant can ask "
        "the follow-up while they are still thinking about it."
    ),
    Why.COMPOUND: (
        "One question wearing three checkboxes. \"Water Heater: Gas / Solar / "
        "Electric\" is not three questions, and a solar system with a gas "
        "backup needs two of them ticked. Given its own control so it cannot end "
        "up half-answered."
    ),
    Why.AGENT_OWNED: (
        "Not something a homeowner knows. Asking which inspection reports will "
        "be attached to the transfer is asking them to do their agent's job, so "
        "these never appear in the seller's flow at all."
    ),
}
