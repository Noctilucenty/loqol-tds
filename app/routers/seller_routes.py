"""The seller's side. Everything here is scoped by the link token.

There is no seller account, no password and no session cookie: the token in the
path is the credential, resolved on every request. That keeps the blast radius of
a leaked link to exactly one disclosure, and makes revocation instant.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..auth import resolve_seller_token
from ..db import get_db
from ..models import (
    Answer, AnswerSource, AnswerStatus, Deal, DisclosureSession, Flag, FlagState,
    SessionStatus, utcnow,
)
from ..schemas import AnswerIn, form_spec
from ..tds.questions import QUESTIONS_BY_ID
from ..services import (
    answers_dict, next_question_id, progress, sync_flags, unanswered_required, write_answer,
)
from ..tds.rules import RULES

router = APIRouter(prefix="/api/s", tags=["seller"])
RULES_BY_ID = {r.id: r for r in RULES}


def _session(token: str, request: Request, db: Session) -> DisclosureSession:
    return resolve_seller_token(db, token, request)


def _state(db: Session, ds: DisclosureSession) -> dict:
    deal = db.get(Deal, ds.deal_id)
    answers = answers_dict(db, ds.id)
    rows = {
        r.question_id: r
        for r in db.scalars(select(Answer).where(Answer.session_id == ds.id))
    }
    flags = db.scalars(
        select(Flag).where(Flag.session_id == ds.id, Flag.state == FlagState.OPEN)
    ).all()
    return {
        "property": {
            "address": deal.property_address,
            "city": deal.city,
            "county": deal.county,
        },
        "sellerName": deal.seller_name,
        "agentName": deal.agent.name if deal.agent else "",
        "status": ds.status.value,
        "cursor": ds.cursor_question_id or next_question_id(answers),
        "answers": {
            qid: {
                "value": r.value.get("v"),
                "status": r.status.value,
                "source": r.source.value,
                "revision": r.revision,
            }
            for qid, r in rows.items()
        },
        "progress": progress(answers),
        "flags": [
            {
                "id": f.id,
                "ruleId": f.rule_id,
                "severity": f.severity,
                "questionIds": f.question_ids,
                "message": f.message,
                "prompt": RULES_BY_ID[f.rule_id].prompt if f.rule_id in RULES_BY_ID else
                          "You answered this one twice. Which is right?",
            }
            for f in flags
        ],
        "missingRequired": unanswered_required(answers),
    }


@router.get("/{token}")
def open_disclosure(token: str, request: Request, db: Session = Depends(get_db)):
    """Everything needed to render the seller experience, in one round trip."""
    ds = _session(token, request, db)
    return {"form": form_spec("seller"), **_state(db, ds)}


@router.get("/{token}/state")
def get_state(token: str, request: Request, db: Session = Depends(get_db)):
    """Cheap poll: state without the form definition."""
    return _state(db, _session(token, request, db))


@router.put("/{token}/answers")
def put_answer(
    token: str,
    body: AnswerIn,
    request: Request,
    db: Session = Depends(get_db),
):
    """Save one answer.

    Called on every change, not on a Save button. Closing the tab therefore costs
    at most the question currently on screen, and the resume cursor moves with
    each write.
    """
    ds = _session(token, request, db)
    if ds.status in (SessionStatus.SENT_FOR_SIGNATURE, SessionStatus.COMPLETED):
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            "This disclosure has already been sent for signature. Contact your agent to reopen it.",
        )

    existing = db.scalar(
        select(Answer).where(
            Answer.session_id == ds.id, Answer.question_id == body.question_id
        )
    )
    stale = (
        body.known_revision is not None
        and existing is not None
        and existing.revision > body.known_revision
    )

    try:
        row = write_answer(
            db, ds.id, body.question_id, body.value,
            status=AnswerStatus(body.status),
            source=AnswerSource(body.source),
            transcript=body.transcript,
        )
    except ValueError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc)) from exc

    return {
        "questionId": row.question_id,
        "revision": row.revision,
        # True when the other lane had already moved this answer on. The client
        # shows a quiet "the voice assistant also answered this" note rather than
        # throwing the seller's input away.
        "supersededOtherLane": stale,
        **_state(db, ds),
    }


@router.post("/{token}/answers/commit-group")
def commit_group(
    token: str,
    body: dict,
    request: Request,
    db: Session = Depends(get_db),
):
    """Commit the implied "no" for every untapped tile when a grid is left.

    The client must not decide which of those are already answered. It holds an
    optimistic copy that can lag a round trip behind, and acting on it lets a
    freshly tapped item get overwritten with the false it was about to skip. The
    server owns the read-check-write, so the decision is made against committed
    state under one transaction.
    """
    ds = _session(token, request, db)
    ids = [qid for qid in (body.get("questionIds") or []) if qid in QUESTIONS_BY_ID]
    if not ids:
        return _state(db, ds)

    already = {
        row.question_id
        for row in db.scalars(
            select(Answer).where(
                Answer.session_id == ds.id, Answer.question_id.in_(ids)
            )
        )
    }
    for qid in ids:
        if qid not in already:
            write_answer(
                db, ds.id, qid, False,
                status=AnswerStatus.ANSWERED,
                source=AnswerSource.FORM,
                advance_cursor=False,
            )
    return _state(db, ds)


@router.post("/{token}/submit")
def submit(token: str, request: Request, db: Session = Depends(get_db)):
    """Seller marks the disclosure complete and hands it to their agent."""
    ds = _session(token, request, db)
    answers = answers_dict(db, ds.id)
    missing = unanswered_required(answers)
    hard = [
        f for f in sync_flags(db, ds.id)
        if f.severity == "hard" and not f.rule_id.startswith("conflict:")
    ]
    if missing or hard:
        return {
            "ok": False,
            "missingRequired": missing,
            "hardFlags": [{"id": f.id, "message": f.message} for f in hard],
        }
    ds.status = SessionStatus.READY_FOR_REVIEW
    ds.submitted_at = utcnow()
    db.commit()
    return {"ok": True, **_state(db, ds)}


@router.post("/{token}/flags/{flag_id}/resolve")
def resolve_flag(
    token: str,
    flag_id: str,
    body: dict,
    request: Request,
    db: Session = Depends(get_db),
):
    """The seller settling a contradiction, in their own words.

    Resolving a flag can carry a corrected answer, which is written through the
    normal path so it lands in the audit trail like any other answer.
    """
    ds = _session(token, request, db)
    flag = db.get(Flag, flag_id)
    if flag is None or flag.session_id != ds.id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Flag not found")

    qid = body.get("questionId")
    if qid:
        write_answer(
            db, ds.id, qid, body.get("value"),
            status=AnswerStatus(body.get("status", "answered")),
            source=AnswerSource.FORM,
            advance_cursor=False,
        )

    flag.state = FlagState.RESOLVED
    flag.resolution = {"note": body.get("note", ""), "by": "seller"}
    flag.resolved_at = utcnow()
    db.commit()
    return _state(db, ds)
