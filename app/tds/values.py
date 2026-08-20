"""Turning whatever arrived into the value a question can actually hold.

This used to live inside the voice router, which meant the two lanes disagreed
about what an answer *is*. The tap lane could store the string "0" for a count,
or a bare `False` for a yes/no/unknown question, or a scalar where a list
belonged - and each of those produced a wrong box on a sworn legal document, or
a 500 that bricked the seller's link.

Every write now goes through `coerce`, so `services.write_answer` really is the
single path its docstring claims.

Two rules that matter more than the rest:

*   **Nothing unrecognised ever becomes a No.** A bool or yes/no question given a
    phrase this module cannot read comes back as UNKNOWN, never as a negative.
    Printing "No" on a disclosure because a transcript said "yeah, there is" is
    the worst failure this code can produce.
*   **UNKNOWN is only a storable answer where the form can express it.** The
    statutory Yes/No pairs can show it by leaving both boxes clear. A Section A
    checkbox cannot, so an unknown there is recorded as unanswered rather than
    silently printing a tick.
"""

from __future__ import annotations

from typing import Any

from .questions import Question

TRUE_WORDS = {
    "yes", "y", "true", "yeah", "yep", "yup", "correct", "affirmative",
    "sure", "definitely", "1", "on", "checked",
}
FALSE_WORDS = {
    "no", "n", "false", "nope", "nah", "negative", "none", "0", "off", "unchecked",
    "nothing", "never", "neither",
}
UNSURE_WORDS = {
    "unknown", "not sure", "unsure", "i don't know", "i dont know", "dont know",
    "don't know", "no idea", "maybe", "possibly", "can't remember",
    "cannot remember", "not certain",
}


class ValueError_(ValueError):
    """Raised when a value cannot be represented by the question at all."""


def _word(value: Any) -> str:
    return str(value).strip().lower().rstrip(".!?")


def _looks_unsure(value: Any) -> bool:
    """Is this a hedge rather than an answer?

    Prefix matching needs a word boundary. Without one, the street address
    "Maybell Ave, Palo Alto" starts with "maybe" and gets filed as "I don't
    know" - so the seller is shown an empty address box and the question lands
    in the missing-required list.
    """
    text = _word(value)
    if text in UNSURE_WORDS:
        return True
    return any(
        text.startswith(phrase) and (
            len(text) == len(phrase) or not text[len(phrase)].isalnum()
        )
        for phrase in UNSURE_WORDS
    )


def _leading_polarity(value: Any) -> bool | None:
    """Read yes/no off the front of a sentence, or None if it does not start there.

    A realtime model asked for "yes or no" will often hand back the whole
    sentence: "No hazards like asbestos or lead paint have ever turned up." That
    is unambiguously a no, and treating it as unparseable would quietly drop a
    clear answer the seller actually gave. Only the *leading* token counts -
    "the neighbour said no" must not read as a denial.
    """
    text = _word(value)
    if not text:
        return None
    head = text.replace(",", " ").replace(".", " ").split()
    if not head:
        return None
    first = head[0]
    if first in TRUE_WORDS:
        return True
    if first in FALSE_WORDS:
        return False
    return None


def coerce(question: Question, value: Any, status: str = "answered") -> tuple[Any, str]:
    """Return `(value, status)` in the canonical shape for this question.

    `status` is one of answered / unknown / skipped. Raises `ValueError_` when the
    input cannot be represented at all, so the caller can reject the write rather
    than store something that would print wrongly.
    """
    if status == "skipped":
        return None, "skipped"

    # A hedge is only a hedge where the question is asking for a yes or a no.
    # In free text, "not sure which of the two it was" is the answer itself.
    hedged = _looks_unsure(value) and question.kind in ("tri", "bool", "single", "multi", "int")
    if status == "unknown" or hedged:
        # Only the paired Yes/No questions can render "I don't know" honestly, by
        # leaving both boxes clear. Anywhere else it is an absence of an answer.
        return ("unknown", "unknown") if question.kind == "tri" else (None, "unknown")

    match question.kind:
        case "tri":
            if isinstance(value, bool):
                return ("yes" if value else "no"), "answered"
            w = _word(value)
            if w in TRUE_WORDS:
                return "yes", "answered"
            if w in FALSE_WORDS:
                return "no", "answered"
            polarity = _leading_polarity(value)
            if polarity is not None:
                return ("yes" if polarity else "no"), "answered"
            return "unknown", "unknown"

        case "bool":
            if isinstance(value, bool):
                return value, "answered"
            w = _word(value)
            if w in TRUE_WORDS:
                return True, "answered"
            if w in FALSE_WORDS:
                return False, "answered"
            polarity = _leading_polarity(value)
            if polarity is not None:
                return polarity, "answered"
            # Deliberately not False. An unreadable answer is not a denial.
            return None, "unknown"

        case "multi":
            valid = {o.id for o in question.options}
            if value is None:
                return [], "answered"
            items = value if isinstance(value, (list, tuple)) else [value]
            if not all(isinstance(i, (str, int)) for i in items):
                raise ValueError_(f"{question.id} expects a list of options")
            kept = [str(i) for i in items if str(i) in valid]
            return kept, "answered"

        case "single":
            valid = {o.id for o in question.options}
            if value is None:
                return None, "unknown"
            if isinstance(value, (list, tuple)):
                value = value[0] if value else None
            v = str(value)
            if v not in valid:
                raise ValueError_(f"{v!r} is not an option for {question.id}")
            return v, "answered"

        case "int":
            if isinstance(value, bool) or value is None:
                return None, "unknown"
            try:
                n = int(str(value).strip())
            except (TypeError, ValueError):
                return None, "unknown"
            if n < 0:
                raise ValueError_(f"{question.id} cannot be negative")
            return n, "answered"

        case _:  # text, longtext, date
            if value is None:
                return "", "answered"
            if isinstance(value, (list, dict)):
                raise ValueError_(f"{question.id} expects text")
            return str(value).strip(), "answered"
