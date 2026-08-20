"""Wire formats, and the serialised question graph.

The graph is sent to the browser rather than re-declared there. One definition of
what the questions are, what gates them and which lane they default to means the
form UI, the voice agent's tool schema and the PDF renderer cannot disagree about
the form - which is the only way a seller can move between lanes mid-question and
find their work intact.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, EmailStr, Field

from .tds.gating import referenced_ids
from .tds.questions import CHAPTERS, QUESTIONS, SELLER_QUESTIONS, Question
from .tds.routing import RATIONALE


# --------------------------------------------------------------------------
# Form spec
# --------------------------------------------------------------------------

def serialise_question(q: Question) -> dict[str, Any]:
    return {
        "id": q.id,
        "chapter": q.chapter,
        "group": q.group,
        "prompt": q.prompt,
        "kind": q.kind,
        "lane": q.lane.value,
        "why": q.why.value,
        "legal": q.legal,
        "explain": q.explain,
        "example": q.example,
        "needs": q.needs,
        "allowsUnknown": q.allows_unknown,
        "options": [{"id": o.id, "label": o.label} for o in q.options],
        "dependsOn": q.depends_on,
        "dependsOnIds": referenced_ids(q.depends_on),
    }


def form_spec(audience: Literal["seller", "agent"] = "seller") -> dict[str, Any]:
    questions = SELLER_QUESTIONS if audience == "seller" else QUESTIONS
    return {
        "formType": "CA_TDS",
        "title": "California Real Estate Transfer Disclosure Statement",
        "chapters": [
            {
                "id": c.id,
                "title": c.title,
                "blurb": c.blurb,
                "minutes": c.minutes,
                "audience": c.audience,
            }
            for c in CHAPTERS
            if audience == "agent" or c.audience == "seller"
        ],
        "questions": [serialise_question(q) for q in questions],
        "rationale": {k.value: v for k, v in RATIONALE.items()},
    }


# --------------------------------------------------------------------------
# Requests
# --------------------------------------------------------------------------

class LoginIn(BaseModel):
    email: EmailStr
    password: str = Field(min_length=8, max_length=200)


class RegisterIn(LoginIn):
    name: str = Field(min_length=1, max_length=120)
    brokerage: str = ""
    license_number: str = ""


class DealIn(BaseModel):
    property_address: str = Field(min_length=4, max_length=300)
    city: str = ""
    county: str = ""
    seller_name: str = Field(min_length=1, max_length=160)
    seller_email: EmailStr
    co_seller_name: str = ""
    co_seller_email: str = ""


class AnswerIn(BaseModel):
    question_id: str
    value: Any = None
    status: Literal["answered", "unknown", "skipped", "needs_review"] = "answered"
    source: Literal["form", "voice", "agent"] = "form"
    transcript: str | None = None
    #: Revision the client believed it was editing. Used to detect that the other
    #: lane changed this answer while the seller was looking at it.
    known_revision: int | None = None


class FlagResolutionIn(BaseModel):
    keep_question_id: str | None = None
    value: Any = None
    note: str = ""
    action: Literal["resolve", "dismiss"] = "resolve"


# --------------------------------------------------------------------------
# Responses
# --------------------------------------------------------------------------

class AgentOut(BaseModel):
    id: str
    email: str
    name: str
    brokerage: str = ""


class DealOut(BaseModel):
    id: str
    property_address: str
    city: str
    county: str
    seller_name: str
    seller_email: str
    co_seller_name: str = ""
    created_at: datetime
    session_id: str | None = None
    status: str = "draft"
    percent: int = 0
    open_flags: int = 0
    link_issued: bool = False
    link_last_used: datetime | None = None
    submitted_at: datetime | None = None


class AnswerOut(BaseModel):
    question_id: str
    value: Any
    status: str
    source: str
    revision: int
    transcript: str | None = None
    updated_at: datetime


class FlagOut(BaseModel):
    id: str
    rule_id: str
    severity: str
    question_ids: list[str]
    message: str
    prompt: str = ""
    state: str
