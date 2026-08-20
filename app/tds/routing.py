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
        "Closed-set enumeration. Many items, near-zero ambiguity, and the seller is "
        "walking their own house in their head. A tappable grid is both faster and more "
        "accurate than speaking fifty yes/nos, and it lets them scan for what they "
        "forgot. Voice here is actively worse: it serialises a task the eye does in "
        "parallel."
    ),
    Why.PRECISION: (
        "An exact string, number or date. Speech-to-text on addresses, unit numbers and "
        "proper nouns is the highest-error path in any voice UI, and an address typo "
        "propagates onto all three pages of a legal instrument. Tap, always."
    ),
    Why.GATE: (
        "A single binary whose answer visibly opens or closes a whole section. One tap "
        "beats a turn of dialogue, and the seller gets to see the consequence of the "
        "answer immediately."
    ),
    Why.COMPREHENSION: (
        "The seller usually does not know what is being asked. 'Encroachments, easements "
        "or similar matters' is not a question a homeowner can answer as written. The "
        "bottleneck is comprehension, not input, and the fix for comprehension is a "
        "conversation that can rephrase, give an example, and check it landed."
    ),
    Why.NARRATIVE: (
        "A bare yes is unusable here; the form demands the story, with dates, scope and "
        "whether it was repaired. Typing a legal narrative at 10pm on a phone is exactly "
        "where sellers abandon. Speaking is roughly three times faster than thumb-typing "
        "and lets the agent ask the follow-up while the memory is still open."
    ),
    Why.COMPOUND: (
        "One question that resolves to several sub-answers, usually needing a follow-up "
        "before the answer is usable at all. Handled as a single purpose-built control "
        "rather than as loose checkboxes, so it cannot be half-answered."
    ),
    Why.AGENT_OWNED: (
        "Not the seller's knowledge. Asking a homeowner which inspection reports will be "
        "attached to the transfer is asking them to do their agent's job. Routed out of "
        "the seller flow entirely."
    ),
}
