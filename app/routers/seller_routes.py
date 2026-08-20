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
    FrozenDisclosure, answers_dict, next_question_id, progress, settle_flag, sync_flags,
    unanswered_required, write_answer,
)
from ..tds.gating import is_visible
from ..tds.questions import CHAPTERS_BY_ID, QUESTIONS_BY_ID
from ..tds.values import ValueError_
from ..tds.rules import RULES

router = APIRouter(prefix="/api/s", tags=["seller"])
RULES_BY_ID = {r.id: r for r in RULES}


def _session(token: str, request: Request, db: Session) -> DisclosureSession:
    return resolve_seller_token(db, token, request)


def _seller_may_answer(question_id: str, answers: dict) -> None:
    """The seller may only write questions their own flow actually asks.

    Existing in the graph is not enough. Section I belongs to the agent, and a
    question whose gate is currently shut is not being asked at all. The voice
    lane already refused both cases; the tap lane accepted them.
    """
    q = QUESTIONS_BY_ID.get(question_id)
    if q is None:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, f"Unknown question {question_id!r}")
    if CHAPTERS_BY_ID[q.chapter].audience != "seller":
        raise HTTPException(
            status.HTTP_403_FORBIDDEN,
            f"{question_id} is completed by the agent, not the seller",
        )
    if not is_visible(q, answers):
        raise HTTPException(
            status.HTTP_409_CONFLICT, f"{question_id} is not currently being asked"
        )


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
    _seller_may_answer(body.question_id, answers_dict(db, ds.id))

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
            # The lane is decided here, not by the client. Letting a request
            # label itself "agent" both corrupts the audit trail and defeats the
            # cross-lane conflict detector, whose only trigger is a source change.
            source=AnswerSource.FORM,
            transcript=body.transcript,
        )
    except FrozenDisclosure as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from exc
    except (ValueError_, ValueError) as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc)) from exc

    if body.question_id == "P.address" and isinstance(row.value.get("v"), str):
        # The address is deal metadata, not a form answer - it reaches all three
        # page headers through roles.SYSTEM_FIELDS. Without this write-back the
        # seller is told to check it character by character, corrects a wrong
        # ZIP, and then signs three pages carrying the address they rejected.
        corrected = row.value["v"].strip()
        deal = db.get(Deal, ds.deal_id)
        if corrected and deal and deal.property_address != corrected:
            deal.property_address = corrected
            db.commit()

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

    current = answers_dict(db, ds.id)
    # Only plain yes/no inventory tiles carry an implied "no". Writing False into
    # a statutory Yes/No pair would leave both boxes clear while still counting
    # as answered, so the completeness gate would wave through a blank Section B.
    ids = [
        qid for qid in ids
        if QUESTIONS_BY_ID[qid].kind == "bool"
        and CHAPTERS_BY_ID[QUESTIONS_BY_ID[qid].chapter].audience == "seller"
        and is_visible(QUESTIONS_BY_ID[qid], current)
    ]
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
    # A hard flag about questions only the agent can answer must not trap the
    # seller. They have no control to fix it and no way to dismiss it, so it
    # would block "Send to my agent" permanently with nothing on screen to do.
    hard = [
        f for f in sync_flags(db, ds.id)
        if f.severity == "hard"
        and not f.rule_id.startswith("conflict:")
        and any(
            qid in QUESTIONS_BY_ID
            and CHAPTERS_BY_ID[QUESTIONS_BY_ID[qid].chapter].audience == "seller"
            for qid in (f.question_ids or [])
        )
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
        _seller_may_answer(qid, answers_dict(db, ds.id))
        write_answer(
            db, ds.id, qid, body.get("value"),
            status=AnswerStatus(body.get("status", "answered")),
            source=AnswerSource.FORM,
            advance_cursor=False,
        )

    # Settle only if the contradiction is actually gone. Settling
    # unconditionally let a seller close a hard flag by re-tapping the value they
    # already had - a no-op write that stamped a fingerprint over the unchanged
    # answers and permanently suppressed the rule, so the form shipped with the
    # contradiction intact and the hard gate defeated.
    still_firing = {f.rule_id for f in sync_flags(db, ds.id)}
    if flag.rule_id in still_firing and flag.severity == "hard":
        return _state(db, ds)

    settle_flag(db, flag, state=FlagState.RESOLVED, note=body.get("note", ""), by="seller")
    return _state(db, ds)
