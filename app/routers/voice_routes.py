"""The browser voice lane.

The conversation runs entirely in the page over WebRTC against OpenAI's realtime
API. No phone, no phone number, no server in the audio path.

The standing API key never reaches the browser. This endpoint mints a short-lived
client secret scoped to one session, which is the only credential the page ever
holds. Two other limits sit here for the same reason: a hard ceiling on session
length and a per-disclosure hourly cap, because a public demo with an unbounded
realtime socket is an unbounded bill and a seller link is, by design, shareable.

The agent does not free-text its way to an answer. It is given the same question
graph the form renders from, and a `record_answer` tool whose arguments are
validated against that graph server-side before anything is written. A model that
mishears "no" as "yes" is a bug; a model that can invent a question id, or write
a value the form cannot represent, would be a defect in this file.
"""

from __future__ import annotations

from datetime import timedelta

import httpx
from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..auth import resolve_seller_token
from ..config import settings
from ..db import get_db
from ..models import AnswerSource, AnswerStatus, Deal, VoiceSession, utcnow
from ..services import answers_dict, next_question_id, write_answer
from ..tds.gating import is_visible
from ..tds.questions import CHAPTERS_BY_ID, QUESTIONS_BY_ID, SELLER_QUESTIONS

router = APIRouter(prefix="/api/voice", tags=["voice"])

REALTIME_SESSIONS_URL = "https://api.openai.com/v1/realtime/client_secrets"


def _voice_questions(answers: dict) -> list:
    return [
        q for q in SELLER_QUESTIONS
        if q.lane.value == "voice" and is_visible(q, answers)
    ]


def build_instructions(deal: Deal, answers: dict) -> str:
    """The agent's brief.

    Written as constraints rather than persona. The failure modes that matter
    here are leading the witness, accepting a yes without the detail the form
    needs, and treating "I'm not sure" as a no - so those are what the prompt
    spends its words on.
    """
    pending = _voice_questions(answers)
    remaining = [q for q in pending if answers.get(q.id) in (None, "", [])]

    lines = [
        "You are helping a homeowner complete the California Transfer Disclosure "
        "Statement by voice. They are not a lawyer and not an engineer. They are "
        "probably tired and doing this after work.",
        "",
        f"Property: {deal.property_address}.",
        f"Seller: {deal.seller_name}.",
        "",
        "How to talk:",
        "- One question at a time. Short sentences. No preamble, no recap of what "
        "they just said unless you are confirming a correction.",
        "- Ask the question in plain language first. Only read the statutory "
        "wording if they ask what the form actually says.",
        "- If they sound unsure, offer the example before offering an answer. "
        "Never suggest which way to answer.",
        "- If they say they do not know, record it as unknown. Do not talk them "
        "into a yes or a no. On this form 'I don't know' is a real answer and a "
        "safer one than a guess.",
        "- When they say yes to something, you need the detail before moving on: "
        "what happened, roughly when, and whether it was repaired. Ask for "
        "whichever of those is missing, once. Do not interrogate.",
        "- Approximate dates are fine. 'A few years ago' is a usable answer.",
        "",
        "Recording answers:",
        "- Call record_answer as soon as an answer is usable. Do not batch.",
        "- Only use question ids from the list below.",
        "- If they change an earlier answer, call record_answer again with the new "
        "value. The change is tracked; do not argue with them about it.",
        "- Call finish_section when the remaining questions are done.",
        "",
        "Questions still to cover:",
    ]
    for q in remaining:
        lines.append(f"- {q.id}: {q.prompt}")
        if q.explain:
            lines.append(f"    context: {q.explain}")
        if q.example:
            lines.append(f"    example answer: {q.example}")
        if q.needs:
            lines.append(f"    a usable answer needs: {q.needs}")
    if not remaining:
        lines.append("- (none: thank them and call finish_section)")
    return "\n".join(lines)


def build_tools(answers: dict) -> list[dict]:
    ids = [q.id for q in _voice_questions(answers)]
    return [
        {
            "type": "function",
            "name": "record_answer",
            "description": (
                "Record the seller's answer to one question. Call this the moment "
                "the answer is usable, not at the end."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "question_id": {"type": "string", "enum": ids or ["none"]},
                    "value": {
                        "description": (
                            "yes, no, or unknown for yes/no questions. The seller's "
                            "own words, cleaned up, for descriptions."
                        ),
                        "type": ["string", "boolean", "array", "number", "null"],
                    },
                    "status": {"type": "string", "enum": ["answered", "unknown", "skipped"]},
                    "transcript": {
                        "type": "string",
                        "description": "What the seller actually said, verbatim.",
                    },
                },
                "required": ["question_id", "value", "status"],
            },
        },
        {
            "type": "function",
            "name": "finish_section",
            "description": "Call when the questions in this session are done.",
            "parameters": {"type": "object", "properties": {}},
        },
    ]


@router.get("/{token}/config")
def voice_config(token: str, request: Request, db: Session = Depends(get_db)):
    ds = resolve_seller_token(db, token, request)
    answers = answers_dict(db, ds.id)
    cfg = settings()
    return {
        "enabled": cfg.voice_enabled,
        "model": cfg.openai_realtime_model,
        "maxSeconds": cfg.voice_session_max_seconds,
        "pendingQuestionIds": [q.id for q in _voice_questions(answers)
                               if answers.get(q.id) in (None, "", [])],
    }


@router.post("/{token}/session")
async def mint_session(token: str, request: Request, db: Session = Depends(get_db)):
    """Mint an ephemeral realtime client secret for this browser."""
    cfg = settings()
    ds = resolve_seller_token(db, token, request)

    if not cfg.voice_enabled:
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE,
            "The voice assistant is not configured on this deployment. "
            "Every question can still be answered by tapping.",
        )

    recent = db.scalar(
        select(func.count(VoiceSession.id)).where(
            VoiceSession.session_id == ds.id,
            VoiceSession.created_at > utcnow() - timedelta(hours=1),
        )
    ) or 0
    if recent >= cfg.voice_sessions_per_hour:
        raise HTTPException(
            status.HTTP_429_TOO_MANY_REQUESTS,
            "Too many voice sessions in the last hour. You can keep going by tapping.",
        )

    deal = db.get(Deal, ds.deal_id)
    answers = answers_dict(db, ds.id)

    payload = {
        "session": {
            "type": "realtime",
            "model": cfg.openai_realtime_model,
            "instructions": build_instructions(deal, answers),
            "tools": build_tools(answers),
            "audio": {
                "input": {
                    "transcription": {"model": "gpt-4o-mini-transcribe"},
                    "turn_detection": {
                        "type": "semantic_vad",
                        # Sellers pause mid-sentence while they remember. Cutting
                        # them off at the pause is the fastest way to make a voice
                        # UI feel hostile, so the agent waits.
                        "eagerness": "low",
                    },
                },
                "output": {"voice": "cedar"},
            },
        },
        "expires_after": {"anchor": "created_at", "seconds": 600},
    }

    async with httpx.AsyncClient(timeout=30.0) as client:
        r = await client.post(
            REALTIME_SESSIONS_URL,
            headers={
                "Authorization": f"Bearer {cfg.openai_api_key}",
                "Content-Type": "application/json",
            },
            json=payload,
        )
    if r.status_code >= 400:
        raise HTTPException(
            status.HTTP_502_BAD_GATEWAY,
            f"Could not start the voice assistant ({r.status_code}). "
            "You can keep going by tapping.",
        )
    data = r.json()

    db.add(VoiceSession(
        session_id=ds.id,
        model=cfg.openai_realtime_model,
        expires_at=utcnow() + timedelta(seconds=cfg.voice_session_max_seconds),
    ))
    db.commit()

    return {
        "clientSecret": data.get("value") or data.get("client_secret", {}).get("value"),
        "model": cfg.openai_realtime_model,
        "expiresAt": data.get("expires_at"),
        "maxSeconds": cfg.voice_session_max_seconds,
    }


@router.post("/{token}/answer")
def voice_answer(token: str, body: dict, request: Request, db: Session = Depends(get_db)):
    """Apply one `record_answer` tool call.

    The model proposes; this validates. A question id that is not in the graph,
    or not currently visible, is rejected rather than stored - the browser is not
    a trusted source just because a model is driving it.
    """
    ds = resolve_seller_token(db, token, request)
    qid = body.get("question_id")
    q = QUESTIONS_BY_ID.get(qid)
    if q is None:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, f"Unknown question {qid!r}")

    answers = answers_dict(db, ds.id)
    if not is_visible(q, answers):
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            f"{qid} is not currently being asked",
        )

    raw_status = body.get("status", "answered")
    value = body.get("value")
    value, coerced_status = _coerce(q, value, raw_status)

    row = write_answer(
        db, ds.id, qid, value,
        status=AnswerStatus(coerced_status),
        source=AnswerSource.VOICE,
        transcript=body.get("transcript"),
    )
    answers = answers_dict(db, ds.id)
    return {
        "ok": True,
        "questionId": qid,
        "value": value,
        "status": coerced_status,
        "revision": row.revision,
        "next": next_question_id(answers, after=qid),
    }


def _coerce(q, value, status_hint: str) -> tuple:
    """Force a model-supplied value into the shape the question actually holds."""
    if status_hint == "unknown" or (isinstance(value, str) and value.strip().lower() in
                                    {"unknown", "not sure", "don't know", "dont know"}):
        return (None if q.kind != "tri" else "unknown"), "unknown"
    if status_hint == "skipped":
        return None, "skipped"

    match q.kind:
        case "tri":
            if isinstance(value, bool):
                return ("yes" if value else "no"), "answered"
            v = str(value).strip().lower()
            if v in {"yes", "y", "true"}:
                return "yes", "answered"
            if v in {"no", "n", "false"}:
                return "no", "answered"
            return "unknown", "unknown"
        case "bool":
            if isinstance(value, bool):
                return value, "answered"
            return str(value).strip().lower() in {"yes", "y", "true"}, "answered"
        case "multi":
            valid = {o.id for o in q.options}
            items = value if isinstance(value, list) else [value]
            return [str(v) for v in items if str(v) in valid], "answered"
        case "single":
            valid = {o.id for o in q.options}
            return (str(value) if str(value) in valid else None), "answered"
        case "int":
            try:
                return int(value), "answered"
            except (TypeError, ValueError):
                return None, "unknown"
        case _:
            return ("" if value is None else str(value)), "answered"
