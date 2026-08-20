"""Whether a question is currently being asked.

Conditions are written on the question as short readable strings rather than
lambdas, because they have to survive a trip to the browser and to the voice
agent's tool schema. The grammar is deliberately tiny:

    A.garage is true
    B.gate is yes
    A.water_supply contains private
    A.pool is true or A.hot_tub is true
    any C.* is yes

Gating is evaluated identically on the server and in the client, and the server
is authoritative: `resolve()` refuses to print an answer whose question is not
currently visible, so a pool heater answer cannot survive the seller going back
and saying there is no pool.
"""

from __future__ import annotations

from typing import Any

from .questions import QUESTIONS_BY_ID, Question

TRUTHY = {"true": True, "false": False}


def _literal(token: str) -> Any:
    token = token.strip()
    if token in TRUTHY:
        return TRUTHY[token]
    return token


def _clause(text: str, answers: dict[str, Any]) -> bool:
    text = text.strip()

    if text.startswith("any "):
        # any C.* is yes
        rest = text[4:]
        pattern, _, expected = rest.partition(" is ")
        prefix = pattern.strip().rstrip("*")
        want = _literal(expected)
        return any(
            v == want
            for k, v in answers.items()
            if k.startswith(prefix) and k in QUESTIONS_BY_ID
        )

    if " contains " in text:
        qid, _, option = text.partition(" contains ")
        value = answers.get(qid.strip())
        if value is None:
            return False
        if isinstance(value, str):
            return value == option.strip()
        return option.strip() in value

    if " is " in text:
        qid, _, expected = text.partition(" is ")
        return answers.get(qid.strip()) == _literal(expected)

    raise ValueError(f"unparseable condition: {text!r}")


def evaluate(condition: str | None, answers: dict[str, Any]) -> bool:
    if not condition:
        return True
    return any(_clause(part, answers) for part in condition.split(" or "))


def is_visible(question: Question, answers: dict[str, Any]) -> bool:
    return evaluate(question.depends_on, answers)


def visible_questions(questions: list[Question], answers: dict[str, Any]) -> list[Question]:
    return [q for q in questions if is_visible(q, answers)]


def referenced_ids(condition: str | None) -> list[str]:
    """Question ids a condition depends on, for cache invalidation in the UI."""
    if not condition:
        return []
    ids: list[str] = []
    for part in condition.split(" or "):
        part = part.strip()
        if part.startswith("any "):
            continue
        for sep in (" contains ", " is "):
            if sep in part:
                ids.append(part.partition(sep)[0].strip())
                break
    return ids
