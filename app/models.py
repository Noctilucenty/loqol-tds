"""The data model.

Two decisions carry most of the weight here.

**Answers are current-value rows; history is a separate append-only log.**
`Answer` holds exactly one row per (session, question) under a unique
constraint, and writes upsert into it. `AnswerEvent` records every write that
ever happened, including the ones that were overwritten.

The reason is that a TDS is a sworn statement, and the two questions asked of it
pull in opposite directions. Rendering the form asks "what is the answer" - cheap
against a current-value table, an expensive fold against an event log. A dispute
asks "what did the seller say, and when, and through which channel" - impossible
against a current-value table alone. Keeping both is the only shape that answers
both, and the log is what lets the app notice that the voice agent and the form
disagree instead of silently letting the last writer win.

**"I don't know" is a first-class answer.** `AnswerStatus.UNKNOWN` is not
`False` and not absent. On a disclosure form, answering No when the honest answer
is "I never checked" is the single most common way a seller creates liability for
themselves, so the model refuses to collapse the two and the renderer leaves both
statutory boxes clear.
"""

from __future__ import annotations

import enum
import uuid
from datetime import datetime, timezone

from sqlalchemy import (
    JSON, Boolean, DateTime, Enum, ForeignKey, Index, Integer, String, Text, UniqueConstraint,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def new_id() -> str:
    return uuid.uuid4().hex


class Base(DeclarativeBase):
    pass


class AnswerStatus(enum.StrEnum):
    ANSWERED = "answered"
    UNKNOWN = "unknown"       # seller explicitly does not know
    SKIPPED = "skipped"       # seller chose to come back to it
    NEEDS_REVIEW = "needs_review"  # captured, but the agent flagged it


class AnswerSource(enum.StrEnum):
    FORM = "form"
    VOICE = "voice"
    AGENT = "agent"
    SYSTEM = "system"


class SessionStatus(enum.StrEnum):
    DRAFT = "draft"
    IN_PROGRESS = "in_progress"
    READY_FOR_REVIEW = "ready_for_review"
    SENT_FOR_SIGNATURE = "sent_for_signature"
    COMPLETED = "completed"


class FlagState(enum.StrEnum):
    OPEN = "open"
    RESOLVED = "resolved"
    DISMISSED = "dismissed"


class Agent(Base):
    __tablename__ = "agents"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    email: Mapped[str] = mapped_column(String(320), unique=True, index=True)
    password_hash: Mapped[str] = mapped_column(String(255))
    name: Mapped[str] = mapped_column(String(120))
    brokerage: Mapped[str] = mapped_column(String(160), default="")
    license_number: Mapped[str] = mapped_column(String(60), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    deals: Mapped[list["Deal"]] = relationship(back_populates="agent", cascade="all, delete-orphan")


class AgentSession(Base):
    """Server-side session. A cookie carries the id; revocation is a row update.

    Deliberately not a JWT. A stateless token cannot be revoked before it
    expires, which is the wrong default for a tool that opens a legal document.
    """

    __tablename__ = "agent_sessions"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    agent_id: Mapped[str] = mapped_column(ForeignKey("agents.id", ondelete="CASCADE"), index=True)
    token_hash: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=None)
    user_agent: Mapped[str] = mapped_column(String(300), default="")


class Deal(Base):
    __tablename__ = "deals"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    agent_id: Mapped[str] = mapped_column(ForeignKey("agents.id", ondelete="CASCADE"), index=True)

    property_address: Mapped[str] = mapped_column(String(300))
    city: Mapped[str] = mapped_column(String(120), default="")
    county: Mapped[str] = mapped_column(String(120), default="")

    seller_name: Mapped[str] = mapped_column(String(160))
    seller_email: Mapped[str] = mapped_column(String(320))
    co_seller_name: Mapped[str] = mapped_column(String(160), default="")
    co_seller_email: Mapped[str] = mapped_column(String(320), default="")

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    agent: Mapped[Agent] = relationship(back_populates="deals")
    sessions: Mapped[list["DisclosureSession"]] = relationship(
        back_populates="deal", cascade="all, delete-orphan"
    )


class DisclosureSession(Base):
    """One seller filling out one form for one deal."""

    __tablename__ = "disclosure_sessions"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    deal_id: Mapped[str] = mapped_column(ForeignKey("deals.id", ondelete="CASCADE"), index=True)
    form_type: Mapped[str] = mapped_column(String(40), default="CA_TDS")
    status: Mapped[SessionStatus] = mapped_column(Enum(SessionStatus), default=SessionStatus.DRAFT)

    #: Where to drop the seller back in. Written on every answer, so closing the
    #: tab costs at most the question currently on screen.
    cursor_question_id: Mapped[str | None] = mapped_column(String(64), default=None)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=None)
    submitted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=None)

    docuseal_submission_id: Mapped[int | None] = mapped_column(Integer, default=None)
    docuseal_slug: Mapped[str | None] = mapped_column(String(80), default=None)
    completed_pdf_url: Mapped[str | None] = mapped_column(Text, default=None)

    deal: Mapped[Deal] = relationship(back_populates="sessions")
    answers: Mapped[list["Answer"]] = relationship(
        back_populates="session", cascade="all, delete-orphan"
    )
    tokens: Mapped[list["AccessToken"]] = relationship(
        back_populates="session", cascade="all, delete-orphan"
    )


class AccessToken(Base):
    """The seller's credential. The link *is* the login.

    Only a hash is stored, so a dump of this table does not let anyone open a
    seller's disclosure. The plaintext exists exactly once, in the message the
    agent sends.
    """

    __tablename__ = "access_tokens"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    session_id: Mapped[str] = mapped_column(
        ForeignKey("disclosure_sessions.id", ondelete="CASCADE"), index=True
    )
    token_hash: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=None)
    last_used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=None)
    use_count: Mapped[int] = mapped_column(Integer, default=0)
    #: Coarse audit trail. Enough to notice a link being passed around.
    last_ip: Mapped[str] = mapped_column(String(64), default="")

    session: Mapped[DisclosureSession] = relationship(back_populates="tokens")


class Answer(Base):
    """Current value. Exactly one row per question per session."""

    __tablename__ = "answers"
    __table_args__ = (
        UniqueConstraint("session_id", "question_id", name="uq_answer_session_question"),
    )

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    session_id: Mapped[str] = mapped_column(
        ForeignKey("disclosure_sessions.id", ondelete="CASCADE"), index=True
    )
    question_id: Mapped[str] = mapped_column(String(64), index=True)

    #: Shape depends on the question kind: bool, "yes"/"no", list[str], str, int.
    value: Mapped[dict] = mapped_column(JSON, default=dict)
    status: Mapped[AnswerStatus] = mapped_column(Enum(AnswerStatus), default=AnswerStatus.ANSWERED)
    source: Mapped[AnswerSource] = mapped_column(Enum(AnswerSource), default=AnswerSource.FORM)
    #: Voice answers carry the sentence they came from, so the agent can check
    #: what the model heard rather than trusting the extracted value.
    transcript: Mapped[str | None] = mapped_column(Text, default=None)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )
    revision: Mapped[int] = mapped_column(Integer, default=1)

    session: Mapped[DisclosureSession] = relationship(back_populates="answers")


class AnswerEvent(Base):
    """Append-only. Every write, including the ones that were superseded."""

    __tablename__ = "answer_events"
    __table_args__ = (Index("ix_events_session_question", "session_id", "question_id"),)

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    session_id: Mapped[str] = mapped_column(
        ForeignKey("disclosure_sessions.id", ondelete="CASCADE"), index=True
    )
    question_id: Mapped[str] = mapped_column(String(64))
    value: Mapped[dict] = mapped_column(JSON, default=dict)
    previous_value: Mapped[dict | None] = mapped_column(JSON, default=None)
    status: Mapped[AnswerStatus] = mapped_column(Enum(AnswerStatus))
    source: Mapped[AnswerSource] = mapped_column(Enum(AnswerSource))
    transcript: Mapped[str | None] = mapped_column(Text, default=None)
    #: "seller" or an agent id - who caused this write.
    actor: Mapped[str] = mapped_column(String(64), default="seller")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class Flag(Base):
    """Something the app noticed that a human should decide about.

    Raised by the consistency rules, and by a contradicting overwrite. Never
    blocks the seller: a flag is queued and surfaced at review, because
    interrupting someone mid-recall to argue with them is how you lose them.
    """

    __tablename__ = "flags"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    session_id: Mapped[str] = mapped_column(
        ForeignKey("disclosure_sessions.id", ondelete="CASCADE"), index=True
    )
    rule_id: Mapped[str] = mapped_column(String(64), index=True)
    severity: Mapped[str] = mapped_column(String(16), default="soft")  # soft | hard
    question_ids: Mapped[list] = mapped_column(JSON, default=list)
    message: Mapped[str] = mapped_column(Text)
    state: Mapped[FlagState] = mapped_column(Enum(FlagState), default=FlagState.OPEN)
    resolution: Mapped[dict | None] = mapped_column(JSON, default=None)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=None)


class VoiceSession(Base):
    """One browser voice connection. Exists to bound cost and to audit usage."""

    __tablename__ = "voice_sessions"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    session_id: Mapped[str] = mapped_column(
        ForeignKey("disclosure_sessions.id", ondelete="CASCADE"), index=True
    )
    model: Mapped[str] = mapped_column(String(80))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    answers_written: Mapped[int] = mapped_column(Integer, default=0)
