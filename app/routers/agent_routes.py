"""The agent's side: create a deal, send the request, watch it, review it."""

from __future__ import annotations

from datetime import date

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..auth import current_agent, issue_seller_link
from ..config import settings
from ..db import get_db
from ..docuseal import DocuSealClient, DocuSealError, values_for_submission
from ..models import (
    AccessToken, Agent, Answer, AnswerEvent, AnswerSource, AnswerStatus, Deal,
    DisclosureSession, Flag, FlagState, SessionStatus, utcnow,
)
from ..schemas import AnswerIn, DealIn, DealOut, FlagOut, form_spec
from ..services import (
    FrozenDisclosure, answers_dict, progress, settle_flag, sync_flags,
    unanswered_required, write_answer,
)
from ..tds.values import ValueError_
from ..tds.fieldmap import resolve
from ..tds.fill import render, us_date
from ..tds.questions import QUESTIONS_BY_ID
from ..tds.roles import ROLE_LABELS
from ..tds.rules import RULES

router = APIRouter(prefix="/api/agent", tags=["agent"])
RULES_BY_ID = {r.id: r for r in RULES}


def _owned_deal(db: Session, deal_id: str, agent: Agent) -> Deal:
    deal = db.get(Deal, deal_id)
    if deal is None or deal.agent_id != agent.id:
        # 404 rather than 403: an agent should not be able to probe for the
        # existence of another agent's deals.
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Deal not found")
    return deal


def _session_for(db: Session, deal: Deal) -> DisclosureSession:
    row = db.scalar(
        select(DisclosureSession).where(DisclosureSession.deal_id == deal.id)
    )
    if row is None:
        row = DisclosureSession(deal_id=deal.id)
        db.add(row)
        db.commit()
    return row


def _deal_out(db: Session, deal: Deal) -> DealOut:
    ds = _session_for(db, deal)
    answers = answers_dict(db, ds.id)
    flags = db.scalars(
        select(Flag).where(Flag.session_id == ds.id, Flag.state == FlagState.OPEN)
    ).all()
    token = db.scalar(
        select(AccessToken).where(
            AccessToken.session_id == ds.id, AccessToken.revoked_at.is_(None)
        )
    )
    return DealOut(
        id=deal.id,
        property_address=deal.property_address,
        city=deal.city,
        county=deal.county,
        seller_name=deal.seller_name,
        seller_email=deal.seller_email,
        co_seller_name=deal.co_seller_name,
        created_at=deal.created_at,
        session_id=ds.id,
        status=ds.status.value,
        percent=progress(answers)["percent"],
        open_flags=len(flags),
        link_issued=token is not None,
        link_last_used=token.last_used_at if token else None,
        submitted_at=ds.submitted_at,
    )


@router.get("/form-spec")
def agent_form_spec(agent: Agent = Depends(current_agent)):
    """Requires a session. It was the one agent route left unauthenticated."""
    return form_spec("agent")


@router.get("/deals", response_model=list[DealOut])
def list_deals(db: Session = Depends(get_db), agent: Agent = Depends(current_agent)):
    deals = db.scalars(
        select(Deal).where(Deal.agent_id == agent.id).order_by(Deal.created_at.desc())
    ).all()
    return [_deal_out(db, d) for d in deals]


@router.post("/deals", response_model=DealOut, status_code=201)
def create_deal(body: DealIn, db: Session = Depends(get_db), agent: Agent = Depends(current_agent)):
    deal = Deal(agent_id=agent.id, **body.model_dump())
    db.add(deal)
    db.commit()
    _session_for(db, deal)
    return _deal_out(db, deal)


@router.get("/deals/{deal_id}", response_model=DealOut)
def get_deal(deal_id: str, db: Session = Depends(get_db), agent: Agent = Depends(current_agent)):
    return _deal_out(db, _owned_deal(db, deal_id, agent))


@router.post("/deals/{deal_id}/link")
def create_link(
    deal_id: str,
    request: Request,
    db: Session = Depends(get_db),
    agent: Agent = Depends(current_agent),
):
    """Issue (or rotate) the seller's link.

    Rotating revokes the previous one, which is the recovery path if a link is
    forwarded to the wrong person.
    """
    deal = _owned_deal(db, deal_id, agent)
    ds = _session_for(db, deal)
    raw = issue_seller_link(db, ds.id)
    base = str(request.base_url).rstrip("/")
    # The link is a bearer credential for a legal document, so it must not go out
    # as plain http. Behind a TLS-terminating proxy the app sees http even though
    # the browser used https; trust the forwarded scheme, and never downgrade.
    forwarded = request.headers.get("x-forwarded-proto", "").split(",")[0].strip()
    if forwarded == "https" and base.startswith("http://"):
        base = "https://" + base[len("http://"):]
    elif settings().is_production and base.startswith("http://"):
        base = "https://" + base[len("http://"):]
    return {"url": f"{base}/s/{raw}", "expires_days": settings().seller_link_ttl_days}


@router.delete("/deals/{deal_id}/link")
def revoke_link(deal_id: str, db: Session = Depends(get_db), agent: Agent = Depends(current_agent)):
    deal = _owned_deal(db, deal_id, agent)
    ds = _session_for(db, deal)
    n = 0
    for t in db.scalars(
        select(AccessToken).where(
            AccessToken.session_id == ds.id, AccessToken.revoked_at.is_(None)
        )
    ):
        t.revoked_at = utcnow()
        n += 1
    db.commit()
    return {"revoked": n}


@router.get("/deals/{deal_id}/review")
def review(deal_id: str, db: Session = Depends(get_db), agent: Agent = Depends(current_agent)):
    """Everything the agent needs to check the submission before it is signed."""
    deal = _owned_deal(db, deal_id, agent)
    ds = _session_for(db, deal)
    answers = answers_dict(db, ds.id)
    flags = sync_flags(db, ds.id)
    fields, overflow = resolve(answers)

    rows = db.scalars(select(Answer).where(Answer.session_id == ds.id)).all()
    return {
        "deal": _deal_out(db, deal).model_dump(),
        "answers": {
            r.question_id: {
                "value": r.value.get("v"),
                "status": r.status.value,
                "source": r.source.value,
                "revision": r.revision,
                "transcript": r.transcript,
                "updated_at": r.updated_at.isoformat(),
            }
            for r in rows
        },
        "progress": progress(answers),
        "flags": [
            FlagOut(
                id=f.id, rule_id=f.rule_id, severity=f.severity,
                question_ids=f.question_ids, message=f.message,
                prompt=RULES_BY_ID[f.rule_id].prompt if f.rule_id in RULES_BY_ID else "",
                state=f.state.value,
            ).model_dump()
            for f in flags
        ],
        "missing_required": unanswered_required(answers),
        "field_count": len(fields),
        "addendum_blocks": len(overflow),
    }


@router.get("/deals/{deal_id}/history")
def history(deal_id: str, db: Session = Depends(get_db), agent: Agent = Depends(current_agent)):
    """The append-only trail, newest first.

    This is what makes a changed answer auditable rather than merely overwritten.
    """
    deal = _owned_deal(db, deal_id, agent)
    ds = _session_for(db, deal)
    events = db.scalars(
        select(AnswerEvent)
        .where(AnswerEvent.session_id == ds.id)
        .order_by(AnswerEvent.created_at.desc())
        .limit(400)
    ).all()
    return [
        {
            "question_id": e.question_id,
            "prompt": QUESTIONS_BY_ID[e.question_id].prompt if e.question_id in QUESTIONS_BY_ID else e.question_id,
            "value": e.value.get("v"),
            "previous_value": (e.previous_value or {}).get("v"),
            "changed": (e.previous_value or {}).get("v") != e.value.get("v"),
            "status": e.status.value,
            "source": e.source.value,
            "transcript": e.transcript,
            "actor": e.actor,
            "created_at": e.created_at.isoformat(),
        }
        for e in events
    ]


@router.post("/deals/{deal_id}/answers")
def agent_answer(
    deal_id: str,
    body: AnswerIn,
    db: Session = Depends(get_db),
    agent: Agent = Depends(current_agent),
):
    """The agent filling in Section I, or correcting an answer at review."""
    deal = _owned_deal(db, deal_id, agent)
    ds = _session_for(db, deal)
    try:
        write_answer(
            db, ds.id, body.question_id, body.value,
            status=AnswerStatus(body.status),
            source=AnswerSource.AGENT,
            actor=agent.id,
            advance_cursor=False,
        )
    except FrozenDisclosure as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from exc
    except (ValueError_, ValueError) as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc)) from exc
    return {"ok": True}


@router.get("/deals/{deal_id}/preview.pdf")
def preview_pdf(deal_id: str, db: Session = Depends(get_db), agent: Agent = Depends(current_agent)):
    """The filled form, rendered locally. Always available, key or no key."""
    deal = _owned_deal(db, deal_id, agent)
    ds = _session_for(db, deal)
    answers = answers_dict(db, ds.id)
    fields, overflow = resolve(answers)
    pdf = render(
        fields,
        system={
            "property_address": deal.property_address,
            "disclosure_date": us_date((ds.submitted_at or utcnow()).date()),
        },
        overflow=overflow,
    )
    return Response(
        pdf,
        media_type="application/pdf",
        headers={"Content-Disposition": f'inline; filename="TDS-{deal.id[:8]}.pdf"'},
    )


@router.post("/deals/{deal_id}/send-for-signature")
def send_for_signature(
    deal_id: str,
    db: Session = Depends(get_db),
    agent: Agent = Depends(current_agent),
):
    """Push the answers into DocuSeal and open a signing request."""
    cfg = settings()
    deal = _owned_deal(db, deal_id, agent)
    ds = _session_for(db, deal)
    answers = answers_dict(db, ds.id)

    missing = unanswered_required(answers)
    hard = [
        f for f in sync_flags(db, ds.id)
        if f.severity == "hard" and not f.rule_id.startswith("conflict:")
    ]
    if missing or hard:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            {
                "error": "Not ready to sign",
                "missing_required": missing[:20],
                "hard_flags": [f.message for f in hard],
            },
        )

    if not cfg.docuseal_enabled or not cfg.docuseal_template_id:
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE,
            "DocuSeal is not configured. Set DOCUSEAL_API_KEY and DOCUSEAL_TEMPLATE_ID, "
            "or use the local preview.",
        )

    if ds.docuseal_submission_id and ds.status in (
        SessionStatus.SENT_FOR_SIGNATURE, SessionStatus.COMPLETED
    ):
        # Without this a retry creates a second, independently signable copy of
        # the same disclosure, and whichever one the seller signs may not be the
        # one this deal is tracking.
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            {
                "error": "Already sent for signature",
                "submission_id": ds.docuseal_submission_id,
                "hint": "Open the existing signing link rather than creating a second copy.",
            },
        )

    fields, overflow = resolve(answers)
    if overflow:
        # The template is three fixed pages with nowhere to put a continuation
        # sheet. Sending anyway would put the seller's signature on a document
        # ending mid-sentence, pointing at an attachment that does not exist.
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            {
                "error": "An answer is too long for the printed form",
                "detail": (
                    "These need a continuation sheet, which the signature template "
                    "cannot carry yet. Shorten them, or use the local preview, "
                    "which does include the addendum."
                ),
                "answers": [b.split("\n", 1)[0] for b in overflow],
            },
        )

    values = values_for_submission(fields, {
        "property_address": deal.property_address,
        "disclosure_date": us_date(date.today()),
    })

    try:
        client = DocuSealClient()
        result = client.create_submission(
            cfg.docuseal_template_id,
            seller_name=deal.seller_name,
            seller_email=deal.seller_email,
            values=values,
            co_seller=(deal.co_seller_name, deal.co_seller_email) if deal.co_seller_email else None,
            send_email=False,
        )
    except DocuSealError as exc:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, str(exc)) from exc

    ds.docuseal_submission_id = result.submission_id
    ds.status = SessionStatus.SENT_FOR_SIGNATURE
    ds.submitted_at = utcnow()
    db.commit()

    return {
        "submission_id": result.submission_id,
        "sign_url": result.seller_url,
        "submitters": result.submitters,
    }


@router.get("/deals/{deal_id}/signature-status")
def signature_status(deal_id: str, db: Session = Depends(get_db), agent: Agent = Depends(current_agent)):
    deal = _owned_deal(db, deal_id, agent)
    ds = _session_for(db, deal)
    if not ds.docuseal_submission_id:
        return {"status": ds.status.value, "documents": []}
    try:
        data = DocuSealClient().get_submission(ds.docuseal_submission_id)
    except DocuSealError as exc:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, str(exc)) from exc

    submitters = data.get("submitters") or []
    # Every seller-side party must have signed, and there must be at least one.
    # `all()` over an empty generator is True, which flipped a disclosure to
    # "Signed" on the first poll whenever DocuSeal returned no submitters. And
    # "Co-Seller" never matched a startswith("Seller") test, so a co-owner's
    # missing signature counted as complete.
    seller_side = [
        sub for sub in submitters
        if (sub.get("role") or "") in (ROLE_LABELS["seller"], ROLE_LABELS["co_seller"])
    ]
    if seller_side and all(sub.get("status") == "completed" for sub in seller_side):
        ds.status = SessionStatus.COMPLETED
        db.commit()
    return {
        "status": ds.status.value,
        "docuseal_status": data.get("status"),
        "submitters": [
            {"role": s.get("role"), "status": s.get("status"), "completed_at": s.get("completed_at")}
            for s in submitters
        ],
        "documents": data.get("documents", []),
    }


@router.post("/flags/{flag_id}/resolve")
def resolve_flag(
    flag_id: str,
    body: dict,
    db: Session = Depends(get_db),
    agent: Agent = Depends(current_agent),
):
    flag = db.get(Flag, flag_id)
    if flag is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Flag not found")
    ds = db.get(DisclosureSession, flag.session_id)
    _owned_deal(db, ds.deal_id, agent)
    settle_flag(
        db, flag,
        state=FlagState.DISMISSED if body.get("action") == "dismiss" else FlagState.RESOLVED,
        note=body.get("note", ""),
        by=agent.id,
    )
    return {"ok": True}
