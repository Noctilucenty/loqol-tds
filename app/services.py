"""Writing answers, and everything that has to happen when one changes.

This is the only place answers are written. Both lanes - the tap form and the
voice agent - come through `write_answer`, which is what makes "start in one and
finish in the other" true rather than aspirational: there is no second code path
that could drift.

What happens when a seller answers the same question twice:

*   The `answers` row is upserted, so there is exactly one current value and no
    ambiguity about what prints on the form.
*   An `answer_events` row is appended with the previous value, the new value,
    which lane it came from and who caused it. Nothing is lost.
*   `revision` increments, which is what the client sends back to detect that it
    is editing a stale value.
*   If the new value *disagrees* with the old one and arrived from a different
    lane, a flag is raised instead of letting the last writer silently win. A
    seller who taps No and then tells the voice agent "well, actually, yes" has
    not made a mistake - they have remembered something - and the app should
    notice rather than shrug.
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from .models import (
    Answer, AnswerEvent, AnswerSource, AnswerStatus, DisclosureSession, Flag, FlagState,
    SessionStatus, utcnow,
)
from .tds import rules
from .tds.gating import is_visible
from .tds.questions import CHAPTERS, CHAPTERS_BY_ID, QUESTIONS_BY_ID, SELLER_QUESTIONS


def answers_dict(db: Session, session_id: str) -> dict[str, Any]:
    """Current answers as `{question_id: value}`, with unknowns preserved.

    An UNKNOWN answer maps to the string "unknown" rather than to None, because
    None means "not asked yet" and the difference is the whole point.
    """
    out: dict[str, Any] = {}
    for row in db.scalars(select(Answer).where(Answer.session_id == session_id)):
        if row.status == AnswerStatus.SKIPPED:
            continue
        if row.status == AnswerStatus.UNKNOWN:
            out[row.question_id] = "unknown"
        else:
            out[row.question_id] = row.value.get("v")
    return out


def write_answer(
    db: Session,
    session_id: str,
    question_id: str,
    value: Any,
    *,
    status: AnswerStatus = AnswerStatus.ANSWERED,
    source: AnswerSource = AnswerSource.FORM,
    transcript: str | None = None,
    actor: str = "seller",
    advance_cursor: bool = True,
) -> Answer:
    if question_id not in QUESTIONS_BY_ID:
        raise ValueError(f"unknown question {question_id!r}")

    existing = db.scalar(
        select(Answer).where(
            Answer.session_id == session_id, Answer.question_id == question_id
        )
    )
    previous = existing.value if existing else None
    payload = {"v": value}

    if existing is None:
        row = Answer(
            session_id=session_id,
            question_id=question_id,
            value=payload,
            status=status,
            source=source,
            transcript=transcript,
        )
        db.add(row)
    else:
        changed = existing.value.get("v") != value
        if changed and existing.source != source:
            _raise_conflict_flag(db, session_id, question_id, existing, value, source)
        existing.value = payload
        existing.status = status
        existing.source = source
        existing.transcript = transcript or existing.transcript
        existing.revision += 1
        existing.updated_at = utcnow()
        row = existing

    db.add(AnswerEvent(
        session_id=session_id,
        question_id=question_id,
        value=payload,
        previous_value=previous,
        status=status,
        source=source,
        transcript=transcript,
        actor=actor,
    ))

    disclosure = db.get(DisclosureSession, session_id)
    if disclosure is not None:
        if disclosure.started_at is None:
            disclosure.started_at = utcnow()
        if disclosure.status == SessionStatus.DRAFT:
            disclosure.status = SessionStatus.IN_PROGRESS
        if advance_cursor:
            disclosure.cursor_question_id = question_id

    db.commit()
    sync_flags(db, session_id)
    return row


def _raise_conflict_flag(
    db: Session,
    session_id: str,
    question_id: str,
    existing: Answer,
    new_value: Any,
    new_source: AnswerSource,
) -> None:
    q = QUESTIONS_BY_ID[question_id]
    rule_id = f"conflict:{question_id}"
    already = db.scalar(
        select(Flag).where(
            Flag.session_id == session_id,
            Flag.rule_id == rule_id,
            Flag.state == FlagState.OPEN,
        )
    )
    message = (
        f"Answered {_render(existing.value.get('v'))} in the {existing.source.value} lane, "
        f"then {_render(new_value)} in the {new_source.value} lane."
    )
    if already:
        already.message = message
        return
    db.add(Flag(
        session_id=session_id,
        rule_id=rule_id,
        severity="soft",
        question_ids=[question_id],
        message=message,
        # Not a correction. The later answer stands; this only asks the seller to
        # confirm which one they meant, at review, when they are not mid-thought.
        resolution=None,
    ))


def _render(value: Any) -> str:
    if isinstance(value, bool):
        return "yes" if value else "no"
    if isinstance(value, list):
        return ", ".join(str(v) for v in value) or "nothing"
    return str(value)


def sync_flags(db: Session, session_id: str) -> list[Flag]:
    """Re-run the consistency rules and reconcile the open flag set.

    Flags that no longer fire are closed automatically - a seller who fixes a
    contradiction should not have to also dismiss the warning about it.
    """
    current = answers_dict(db, session_id)
    firing = {r.id: r for r in rules.evaluate(current)}

    open_flags = list(db.scalars(
        select(Flag).where(Flag.session_id == session_id, Flag.state == FlagState.OPEN)
    ))
    for flag in open_flags:
        if flag.rule_id.startswith("conflict:"):
            continue
        if flag.rule_id not in firing:
            flag.state = FlagState.RESOLVED
            flag.resolved_at = utcnow()

    existing_ids = {f.rule_id for f in open_flags if f.state == FlagState.OPEN}
    for rule_id, rule in firing.items():
        if rule_id in existing_ids:
            continue
        db.add(Flag(
            session_id=session_id,
            rule_id=rule_id,
            severity=rule.severity,
            question_ids=list(rule.questions),
            message=rule.message,
        ))

    db.commit()
    return list(db.scalars(
        select(Flag).where(Flag.session_id == session_id, Flag.state == FlagState.OPEN)
    ))


def progress(answers: dict[str, Any]) -> dict[str, Any]:
    """Progress in human units.

    Deliberately not "38 of 150 fields". A count that large reads as a threat,
    and it is also a lie: most of those 150 fields are unreachable for any given
    property. Progress is measured over the questions this seller will actually
    be asked, and reported as chapters plus an estimate in minutes.
    """
    chapters: list[dict[str, Any]] = []
    total_visible = 0
    total_done = 0
    minutes_left = 0.0

    for chapter in CHAPTERS:
        if chapter.audience != "seller" or chapter.id == "review":
            continue
        qs = [q for q in SELLER_QUESTIONS if q.chapter == chapter.id and is_visible(q, answers)]
        done = [q for q in qs if answers.get(q.id) not in (None, "", [])]
        total_visible += len(qs)
        total_done += len(done)
        share_left = 1.0 - (len(done) / len(qs)) if qs else 0.0
        minutes_left += chapter.minutes * share_left
        chapters.append({
            "id": chapter.id,
            "title": chapter.title,
            "blurb": chapter.blurb,
            "total": len(qs),
            "answered": len(done),
            "complete": bool(qs) and len(done) == len(qs),
        })

    return {
        "chapters": chapters,
        "answered": total_done,
        "total": total_visible,
        "percent": round(100 * total_done / total_visible) if total_visible else 0,
        "minutes_left": max(round(minutes_left), 1) if total_done < total_visible else 0,
    }


def next_question_id(answers: dict[str, Any], after: str | None = None) -> str | None:
    """The next unanswered, currently-visible seller question."""
    ordered = [q for q in SELLER_QUESTIONS if is_visible(q, answers)]
    start = 0
    if after:
        for i, q in enumerate(ordered):
            if q.id == after:
                start = i + 1
                break
    for q in ordered[start:]:
        if answers.get(q.id) in (None, "", []):
            return q.id
    for q in ordered:
        if answers.get(q.id) in (None, "", []):
            return q.id
    return None


def unanswered_required(answers: dict[str, Any]) -> list[str]:
    return [
        q.id for q in SELLER_QUESTIONS
        if is_visible(q, answers)
        and q.required_when_shown
        and answers.get(q.id) in (None, "", [])
    ]
