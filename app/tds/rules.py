"""Consistency rules over a set of answers.

These are the contradictions this particular form invites. They are not generic
validation - each one is a pair of TDS questions that real sellers really do
answer inconsistently, usually because the two questions sit forty minutes apart
in the interview.

Two severities:

*   `hard` - the two answers cannot both be true. Someone has to choose.
*   `soft` - the combination is unusual and probably a slip, but it can be
    legitimate, so the seller is asked to confirm rather than corrected.

Nothing here blocks progress. Flags are raised, queued, and shown at review.
Interrupting someone mid-recall to argue with them about something they said
half an hour ago is how you lose them at 10pm, and the form is not due yet.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable


@dataclass(frozen=True)
class Rule:
    id: str
    severity: str
    questions: tuple[str, ...]
    message: str
    predicate: Callable[[dict[str, Any]], bool]
    #: What to ask the seller at review time, in their words.
    prompt: str = ""


def _has(a: dict[str, Any], qid: str) -> bool:
    return a.get(qid) is True


def _is(a: dict[str, Any], qid: str, value: Any) -> bool:
    return a.get(qid) == value


def _sel(a: dict[str, Any], qid: str, option: str) -> bool:
    v = a.get(qid) or []
    return option in v if not isinstance(v, str) else v == option


RULES: list[Rule] = [
    Rule(
        id="sewer_and_septic",
        severity="hard",
        questions=("A.public_sewer", "A.septic_tank"),
        message="The property is marked as having both a public sewer connection and a septic tank.",
        prompt="You told us the house is on both a public sewer and a septic tank. Almost every home has one or the other. Which is it?",
        predicate=lambda a: _has(a, "A.public_sewer") and _has(a, "A.septic_tank"),
    ),
    Rule(
        id="substituted_disclosures_conflict",
        severity="hard",
        questions=("I.substituted",),
        message="'No substituted disclosures' is selected alongside one or more actual disclosures.",
        prompt="This transfer is marked as having no substituted disclosures, but reports are also listed.",
        predicate=lambda a: _sel(a, "I.substituted", "none")
        and (_sel(a, "I.substituted", "inspection_reports") or _sel(a, "I.substituted", "additional")),
    ),
    Rule(
        id="barrier_without_pool",
        severity="hard",
        questions=("A.pool", "A.child_barrier"),
        message="A child-resistant pool barrier is recorded, but the property is marked as having no pool.",
        prompt="You mentioned a child-resistant pool barrier but also said there is no pool. Is there a pool?",
        predicate=lambda a: _has(a, "A.child_barrier") and a.get("A.pool") is False,
    ),
    Rule(
        id="gas_appliance_no_gas_supply",
        severity="soft",
        questions=("A.water_heater", "A.gas_supply"),
        message="A gas water heater is recorded, but no gas supply is selected.",
        prompt="You said the water heater runs on gas, but no gas supply is marked. Is the gas from a utility, or a bottled tank?",
        predicate=lambda a: _sel(a, "A.water_heater", "gas") and not (a.get("A.gas_supply") or []),
    ),
    Rule(
        id="gas_starter_no_fireplace",
        severity="soft",
        questions=("A.gas_starter", "A.fireplace_rooms"),
        message="A fireplace gas starter is recorded, but no rooms with fireplaces were listed.",
        prompt="You marked a gas starter, which is a fireplace fitting, but no fireplace rooms were listed. Which rooms have fireplaces?",
        predicate=lambda a: _has(a, "A.gas_starter") and not (a.get("A.fireplace_rooms") or "").strip(),
    ),
    Rule(
        id="remotes_without_opener",
        severity="hard",
        questions=("A.garage_opener", "A.garage_remotes"),
        message="A number of garage remotes is recorded without an automatic opener.",
        prompt="You listed garage door remotes but no automatic opener. Is there an opener?",
        predicate=lambda a: bool(a.get("A.garage_remotes")) and a.get("A.garage_opener") is False,
    ),
    Rule(
        id="defects_but_all_operating",
        severity="soft",
        questions=("A.not_working", "B.gate"),
        message="Significant structural defects are disclosed, but nothing was reported as not in operating condition.",
        prompt=(
            "Earlier you said everything on the appliance list works, and later that there are "
            "significant defects in the house. Both can be true - one is about appliances, the "
            "other about structure. Just confirming they are both right."
        ),
        predicate=lambda a: _is(a, "B.gate", "yes") and _is(a, "A.not_working", "no"),
    ),
    Rule(
        id="defects_yes_no_components",
        severity="hard",
        questions=("B.gate", "B.components"),
        message="Section B is answered Yes but no component is checked, which leaves the form self-contradictory.",
        prompt="You said there are significant defects, but did not say which part of the house. Which ones?",
        predicate=lambda a: _is(a, "B.gate", "yes") and not (a.get("B.components") or []),
    ),
    Rule(
        id="defects_yes_no_explanation",
        severity="hard",
        questions=("B.gate", "B.explain"),
        message="Section B is answered Yes but the required explanation is empty.",
        prompt="The form needs a short description of each defect you checked.",
        predicate=lambda a: _is(a, "B.gate", "yes") and not (a.get("B.explain") or "").strip(),
    ),
    Rule(
        id="not_working_yes_no_detail",
        severity="hard",
        questions=("A.not_working", "A.not_working_detail"),
        message="Something was reported as not in operating condition, but not described.",
        prompt="You said something is not working. Which item, and what does it do wrong?",
        predicate=lambda a: _is(a, "A.not_working", "yes") and not (a.get("A.not_working_detail") or "").strip(),
    ),
    Rule(
        id="awareness_yes_no_explanation",
        severity="hard",
        questions=("C.explain",),
        message="At least one Section C question is Yes, but the shared explanation is empty.",
        prompt="You answered yes to at least one of the sixteen questions. The form needs an explanation for each.",
        predicate=lambda a: any(
            v == "yes" for k, v in a.items() if k.startswith("C.") and k != "C.explain"
        )
        and not (a.get("C.explain") or "").strip(),
    ),
    Rule(
        id="hoa_without_ccrs",
        severity="soft",
        questions=("C.hoa", "C.ccrs"),
        message="A homeowners' association is disclosed but no CC&Rs.",
        prompt="You said there is a homeowners' association but no CC&Rs. Associations almost always come with recorded CC&Rs. Worth a second look.",
        predicate=lambda a: _is(a, "C.hoa", "yes") and _is(a, "C.ccrs", "no"),
    ),
    Rule(
        id="common_area_without_hoa",
        severity="soft",
        questions=("C.common_area", "C.hoa"),
        message="Co-owned common area is disclosed without a homeowners' association.",
        prompt="You mentioned co-owned common area but no association. Who maintains the shared parts?",
        predicate=lambda a: _is(a, "C.common_area", "yes") and _is(a, "C.hoa", "no"),
    ),
    Rule(
        id="unpermitted_but_code_compliant",
        severity="soft",
        questions=("C.no_permits", "C.not_to_code"),
        message="Unpermitted work is disclosed but reported as code-compliant.",
        prompt=(
            "You said work was done without permits, and that it does meet code. That is "
            "possible, but unusual - unpermitted work is rarely inspected. Are you sure it "
            "meets code, or is it closer to 'I don't know'?"
        ),
        predicate=lambda a: _is(a, "C.no_permits", "yes") and _is(a, "C.not_to_code", "no"),
    ),
    Rule(
        id="damage_without_defects",
        severity="soft",
        questions=("C.major_damage", "B.gate"),
        message="Major fire, flood or earthquake damage is disclosed, but no structural defects.",
        prompt=(
            "You disclosed major damage to the property, and separately said there are no "
            "significant structural defects. If the damage was fully repaired that is exactly "
            "right - just confirming."
        ),
        predicate=lambda a: _is(a, "C.major_damage", "yes") and _is(a, "B.gate", "no"),
    ),
    Rule(
        id="not_occupying_high_confidence",
        severity="soft",
        questions=("P.occupying",),
        message="Seller does not occupy the property but answered every awareness question definitively.",
        prompt=(
            "You do not live at the property, and answered all sixteen questions with a firm "
            "yes or no. If any of those are really 'I don't know', that is a legitimate answer "
            "and a safer one."
        ),
        predicate=lambda a: _is(a, "P.occupying", "is_not")
        and len([k for k, v in a.items() if k.startswith("C.") and v in ("yes", "no")]) >= 16,
    ),
]


def evaluate(answers: dict[str, Any]) -> list[Rule]:
    """Rules currently violated by this answer set."""
    hits: list[Rule] = []
    for rule in RULES:
        try:
            if rule.predicate(answers):
                hits.append(rule)
        except Exception:
            # A rule must never be able to break the seller's session.
            continue
    return hits


RULES_BY_ID = {r.id: r for r in RULES}


def flag_prompt(rule_id: str, question_ids: list[str], fallback: str = "") -> str:
    """What to put in front of a human so they can act on a flag.

    Named rules carry their own wording. The `conflict:<question_id>` flags do
    not - they are minted per question when the two lanes disagree, so they
    never appear in RULES_BY_ID and used to fall through to an empty string on
    the agent side. That put "Answered no in the form lane, then yes in the
    voice lane" in the review panel with nothing saying which question it was
    about, which is not something anyone can do anything with.
    """
    rule = RULES_BY_ID.get(rule_id)
    if rule:
        return rule.prompt

    from .questions import QUESTIONS_BY_ID

    for qid in question_ids:
        question = QUESTIONS_BY_ID.get(qid)
        if question:
            return question.prompt
    return fallback
