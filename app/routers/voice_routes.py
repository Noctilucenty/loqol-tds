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

import re

from datetime import timedelta

import httpx
from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..auth import resolve_seller_token
from ..config import settings
from ..db import get_db
from ..models import AnswerSource, AnswerStatus, Deal, VoiceSession, utcnow
from ..services import FrozenDisclosure, answers_dict, next_question_id, write_answer
from ..tds.gating import is_visible
from ..tds.questions import CHAPTERS_BY_ID, QUESTIONS_BY_ID, SELLER_QUESTIONS
from ..tds.values import ValueError_, coerce

router = APIRouter(prefix="/api/voice", tags=["voice"])

REALTIME_SESSIONS_URL = "https://api.openai.com/v1/realtime/client_secrets"


def _voice_questions(answers: dict, scope: str = "voice") -> list:
    """Questions this session may ask.

    `voice` is the routed default: the sixteen comprehension questions and the
    three narratives. `all` is for a seller who chose to do the whole form by
    talking - they get everything still unanswered, including the inventory.

    Offering `all` costs the routing argument nothing. The claim was never that
    tapping is the only sane way to answer fifty checkboxes, it is that tapping
    is *faster* for them. A seller who cannot use a grid at all, or simply
    prefers to talk, must still have a way through the form.
    """
    pool = [q for q in SELLER_QUESTIONS if is_visible(q, answers)]
    if scope != "all":
        pool = [q for q in pool if q.lane.value == "voice"]
    return pool


def build_instructions(deal: Deal, answers: dict, scope: str = "voice") -> str:
    """The agent's brief.

    Written as constraints rather than persona. The failure modes that matter
    here are leading the witness, accepting a yes without the detail the form
    needs, and treating "I'm not sure" as a no - so those are what the prompt
    spends its words on.
    """
    pending = _voice_questions(answers, scope)
    remaining = [q for q in pending if answers.get(q.id) in (None, "", [])]

    # Inventory items are one-word yes/nos with no ambiguity. Spelling out
    # context for each would bury the questions that actually need it, and blow
    # the prompt out to tens of thousands of characters.
    def is_quickfire(q) -> bool:
        return q.kind == "bool" and q.why.value == "enumeration"

    quickfire = [q for q in remaining if is_quickfire(q)]

    # Keep the form's own running order. The address check and the occupancy
    # question come before the inventory, and leading with the run-through made
    # the assistant open on the kitchen, which is a strange way to greet someone.
    first_item = next((i for i, q in enumerate(remaining) if is_quickfire(q)), len(remaining))
    opening = [q for q in remaining[:first_item] if not is_quickfire(q)]
    considered = [q for q in remaining[first_item:] if not is_quickfire(q)]

    lines = [
        "You are helping a homeowner complete the California Transfer Disclosure "
        "Statement by voice. They are not a lawyer and not an engineer. They are "
        "probably tired and doing this after work.",
        "",
        f"Property: {deal.property_address}.",
        f"Seller: {deal.seller_name}.",
        "",
        "How to talk:",
        "- Short sentences. No preamble, and no recapping what they just said "
        "unless you are confirming a correction.",
        "- Ask the quick run-through items a whole group at a time, and the ones "
        "that need care one at a time. Both lists are below.",
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
        "- A run-through group goes back as ONE record_group call covering every "
        "item in it, noes included. Anything else - the questions that need "
        "care - goes back as record_answer, one per answer, as soon as it is "
        "usable.",
        "- NEVER end your turn announcing what is coming. \"Next up is safety "
        "and security\", \"ready to move on\", \"let's do the next group\" - if "
        "you say any of that, ask the group in the same breath. Ending on an "
        "announcement leaves the seller sitting in silence waiting for a "
        "question that never comes, and they have to prompt you to continue. "
        "Every turn you take ends with either a question or the form being "
        "finished.",
        "- Do not read back what you just captured. The seller can see what was "
        "recorded on screen, and repeating eleven items to them is eleven "
        "seconds they did not need to spend. \"Got it\" then straight into the "
        "next question.",
        "- Never record an item the seller has not actually addressed. Silence "
        "is not an \"unknown\" - \"unknown\" is what they say when they do not "
        "know, and it goes on a legal disclosure either way. If they go quiet, "
        "ask again or wait. Do not fill anything in for them, ever, and never "
        "run ahead to groups you have not read out yet.",
        "- Only use question ids from the list below.",
        "- Where a question lists options, `value` must be one of the option ids "
        "exactly as written, or a list of them. Never invent an option, and "
        "never answer one of those with yes or no.",
        "- If they change an earlier answer, call record_answer again with the new "
        "value. The change is tracked; do not argue with them about it.",
        "- Call finish_section when the remaining questions are done.",
        "",
    ]

    def describe(q) -> list[str]:
        out = [f"- {q.id}: {q.prompt}"]
        if q.options:
            # Without this the model guesses. It answered "yes" to a question
            # whose only valid ids are `is` and `is_not`, and the server
            # correctly rejected the write - so the answer was simply lost.
            ids = ", ".join(f'"{o.id}" ({o.label})' for o in q.options)
            kind = "choose one" if q.kind == "single" else "choose any that apply"
            out.append(f"    options, {kind}: {ids}")
        if q.kind == "int":
            out.append("    value must be a whole number.")
        if q.explain:
            out.append(f"    context: {q.explain}")
        if q.example:
            out.append(f"    example answer: {q.example}")
        if q.needs:
            out.append(f"    a usable answer needs: {q.needs}")
        return out

    if not remaining:
        lines.append("Nothing left to cover: thank them and call finish_section.")
        return "\n".join(lines)

    def describe_all(qs) -> None:
        for q in qs:
            lines.extend(describe(q))

    if opening:
        lines.append("START HERE, one at a time, in this order:")
        describe_all(opening)
        lines.append("")

    if quickfire:
        lines += [
            "THEN THE QUICK RUN-THROUGH. Plain 'does the property have this?' items, no "
            "ambiguity, and there are a lot of them. Read out a whole group in "
            "one go and let them answer it in one go.",
            "",
            "Name the group, then read its items in one sentence, using the "
            "plain names only. The id after each name is for record_answer and "
            "must never be spoken - saying \"A.range\" out loud to a homeowner "
            "is gibberish. Take whatever comes back, often a partial list like "
            "\"the first two and the last one, none of the rest\", and record "
            "every item in the group from it, the noes as well as the yeses.",
            "",
            "\"Nothing else\", \"that's all\", \"just those\" mean nothing else IN "
            "THE GROUP YOU JUST READ OUT. They never refer to items in groups "
            "you have not asked about yet - the seller cannot answer for a list "
            "they have not heard.",
            "",
            "\"Unknown\" is for an item they told you they do not know about. It "
            "is NOT for an item they simply did not mention - those two look the "
            "same in the recording and mean very different things on a legal "
            "form. If they skipped some, leave those out of the call and ask "
            "about just those, together, in one short follow-up: \"and the last "
            "two?\". Never invent an item that is not on the list, never re-ask "
            "one they already answered, and do not explain these unless asked.",
            "",
        ]
        by_group: dict[str, list] = {}
        for q in quickfire:
            by_group.setdefault(q.group or "Other", []).append(q)
        for group, items in by_group.items():
            lines.append(f"{group}:")
            lines += [f"  - {q.prompt}   [id {q.id}]" for q in items]
        lines.append("")

    if considered:
        lines.append(
            "AFTER THAT, the questions that need more care. One at a time, and "
            "follow up where the answer is not yet usable."
        )
        describe_all(considered)

    return "\n".join(lines)


def build_tools(answers: dict, scope: str = "voice") -> list[dict]:
    questions = _voice_questions(answers, scope)
    ids = [q.id for q in questions]
    quickfire_ids = [
        q.id for q in questions if q.kind == "bool" and q.why.value == "enumeration"
    ]
    return [
        {
            "type": "function",
            "name": "record_answer",
            "description": (
                "Record the seller's answer to ONE question. For the questions "
                "that need care. Do not use this for run-through items - a "
                "group goes back as a single record_group call, because eleven "
                "of these in a row is eleven round-trips of silence."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "question_id": {"type": "string", "enum": ids or ["none"]},
                    "value": {
                        "description": (
                            "For a yes/no question this must be exactly \"yes\", "
                            "\"no\" or \"unknown\" - one word, no sentence, no "
                            "explanation. Put what they actually said in "
                            "`transcript` instead. For a description question, "
                            "give the seller's own words tidied up. For a "
                            "multiple-choice question, give a list of option ids."
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
            "name": "record_group",
            "description": (
                "Record a whole run-through group at once, from one reply. Use "
                "this for the quick run-through - one call for the entire group, "
                "including the items they said no to. Never make several "
                "record_answer calls where one record_group would do: the "
                "seller is sitting in silence while you write."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "items": {
                        "type": "array",
                        "description": "Every item in the group you just read out.",
                        "items": {
                            "type": "object",
                            "properties": {
                                "id": {"type": "string", "enum": quickfire_ids or ["none"]},
                                "value": {
                                    "type": "string",
                                    "enum": ["yes", "no", "unknown"],
                                    "description": (
                                        "\"unknown\" ONLY where the seller said "
                                        "they do not know. If they did not "
                                        "mention this item, or you are not sure "
                                        "what they meant, leave it out of the "
                                        "call entirely and ask - do not guess "
                                        "and do not use \"unknown\" as a "
                                        "placeholder."
                                    ),
                                },
                            },
                            "required": ["id", "value"],
                        },
                    },
                    "transcript": {
                        "type": "string",
                        "description": "What the seller said for the group, once.",
                    },
                },
                "required": ["items"],
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
async def mint_session(
    token: str,
    request: Request,
    db: Session = Depends(get_db),
    scope: str = "voice",
):
    """Mint an ephemeral realtime client secret for this browser.

    `scope=all` widens the session to every question still unanswered, for a
    seller who chose to do the whole form by talking.
    """
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
            "instructions": build_instructions(deal, answers, scope),
            "tools": build_tools(answers, scope),
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
    try:
        value, coerced_status = _coerce(q, body.get("value"), raw_status)
    except ValueError_ as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc)) from exc

    try:
        row = write_answer(
            db, ds.id, qid, value,
            status=AnswerStatus(coerced_status),
            source=AnswerSource.VOICE,
            transcript=body.get("transcript"),
        )
    except FrozenDisclosure as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from exc
    answers = answers_dict(db, ds.id)
    return {
        "ok": True,
        "questionId": qid,
        "value": value,
        "status": coerced_status,
        "revision": row.revision,
        "next": _next_ask(answers, "all"),
    }


_HEDGE = re.compile(
    r"\b(?:not\s+sure|unsure|no\s+idea|don'?t\s+know|do\s+not\s+know|dunno|"
    r"can'?t\s+remember|cannot\s+remember|maybe|possibly|i\s+think|unknown|"
    r"not\s+certain|no\s+clue)\b",
    re.IGNORECASE,
)


def _next_ask(answers: dict, scope: str) -> dict | None:
    """What the assistant should say next, handed back with every tool result.

    A rule in the system prompt was not holding: the assistant would record an
    answer, say "ready to move on to safety and security", and end its turn -
    leaving the seller sitting in silence waiting for a question that never
    came, until they prompted it to continue. That is most of the "sometimes it
    takes ages" complaint.

    So the next question travels with the result of the last one. The model
    cannot skim past its own tool output the way it skims a long brief.
    """
    remaining = _voice_questions(answers, scope)
    if not remaining:
        return None

    head = remaining[0]
    if head.kind == "bool" and head.why.value == "enumeration":
        group = head.group
        items = [
            {"id": q.id, "name": q.prompt}
            for q in remaining
            if q.group == group and q.kind == "bool" and q.why.value == "enumeration"
        ]
        return {
            "say": f"Read out the {group} group now, in this turn, and wait for their answer.",
            "group": group,
            "items": items,
        }
    return {
        "say": "Ask this next, now, in this turn.",
        "question_id": head.id,
        "prompt": head.prompt,
    }


def _apply_one(db, ds, qid, value, raw_status, transcript, answers):
    """Validate and store one proposed answer. Raises HTTPException on refusal.

    Shared by record_answer and record_group so a grouped write cannot drift
    into being the lenient one.
    """
    q = QUESTIONS_BY_ID.get(qid)
    if q is None:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, f"Unknown question {qid!r}")
    if not is_visible(q, answers):
        raise HTTPException(status.HTTP_409_CONFLICT, f"{qid} is not currently being asked")

    try:
        value, coerced_status = _coerce(q, value, raw_status)
    except ValueError_ as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc)) from exc

    try:
        row = write_answer(
            db, ds.id, qid, value,
            status=AnswerStatus(coerced_status),
            source=AnswerSource.VOICE,
            transcript=transcript,
        )
    except FrozenDisclosure as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from exc
    return value, coerced_status, row


@router.post("/{token}/answers")
def voice_answers(token: str, body: dict, request: Request, db: Session = Depends(get_db)):
    """Apply a whole `record_group` call in one request.

    A run-through group is seven or eight items, and doing them as seven
    separate tool calls meant the model emitting seven full JSON objects and the
    browser making seven round-trips before it could say anything back. That is
    several seconds of silence after the seller has already answered, which on a
    voice call reads as the thing being broken.

    Refusals are per item: one bad id does not throw away the six good answers
    next to it, because the seller did say them and would have to say them again.
    """
    ds = resolve_seller_token(db, token, request)
    items = body.get("items")
    if not isinstance(items, list) or not items:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "items must be a non-empty list")

    transcript = body.get("transcript")

    # One call, one group. Without this the cheapest way for the model to
    # "finish" is to emit every remaining item at once, which it will do - it
    # filled sixty-eight items with "unknown" off three sentences in testing.
    # On a disclosure that is fabrication, not efficiency.
    groups = {
        QUESTIONS_BY_ID[i["id"]].group
        for i in items
        if isinstance(i, dict) and i.get("id") in QUESTIONS_BY_ID
    }
    if len(groups) > 1:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            "One call, one group - these span "
            + ", ".join(sorted(g or "(none)" for g in groups))
            + ". Send record_group again with only the items from the group you "
            "just read out, and leave the rest for when you ask about them. Do "
            "not fall back to one record_answer per item; that is slow for the "
            "seller and these are run-through items.",
        )

    recorded: list[dict] = []
    refused: list[dict] = []

    # An "unknown" that lands on top of a definite answer, with nothing in what
    # the seller said suggesting doubt, is the model slipping rather than the
    # seller changing their mind. Refuse just that item.
    #
    # This deliberately does not reuse values._looks_unsure. That one matches a
    # whole answer from its start, on purpose - it has to, or the street address
    # "Maybell Ave" reads as "maybe". Here we are scanning a whole spoken
    # sentence for a hedge anywhere in it, which is the opposite question.
    sounds_unsure = bool(transcript) and _HEDGE.search(str(transcript)) is not None

    for item in items:
        if not isinstance(item, dict):
            refused.append({"questionId": None, "error": "not an object"})
            continue
        qid = item.get("id") or item.get("question_id")
        answers = answers_dict(db, ds.id)

        if (
            str(item.get("value", "")).lower() == "unknown"
            and not sounds_unsure
            and answers.get(qid) is not None
        ):
            refused.append({
                "questionId": qid,
                "error": "already answered and nothing in what they said "
                         "suggests doubt - leave it as it is",
            })
            continue
        try:
            value, coerced_status, _row = _apply_one(
                db, ds, qid, item.get("value"),
                item.get("status", "answered"), transcript, answers,
            )
        except HTTPException as exc:
            # A frozen disclosure is not per-item - nothing else will land either.
            if exc.status_code == status.HTTP_409_CONFLICT and "id" not in str(exc.detail):
                if not recorded:
                    raise
            refused.append({"questionId": qid, "error": str(exc.detail)})
            continue
        recorded.append({"questionId": qid, "value": value, "status": coerced_status})

    return {
        "ok": True,
        "recorded": recorded,
        "refused": refused,
        "next": _next_ask(answers_dict(db, ds.id), "all"),
    }


def _coerce(q, value, status_hint: str) -> tuple:
    """Delegate to the one place that knows what a question can hold.

    This used to be a second, subtly different implementation. Its `bool` branch
    turned anything it did not recognise into `False` with status "answered" - so
    "Yes, there is a public sewer" printed a No on a legal disclosure.
    """
    return coerce(q, value, status_hint)
