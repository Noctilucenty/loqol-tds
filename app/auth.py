"""Two different problems, two different mechanisms.

**Agents** get email and password. The password is hashed with Argon2id. A
successful login creates a row in `agent_sessions` and sets an HttpOnly,
SameSite=Lax, Secure cookie holding a 256-bit random token; only the SHA-256 of
that token is stored. It is a server-side session rather than a JWT on purpose -
a stateless token cannot be revoked before it expires, and "log this person out
now" is a requirement for a tool that opens legal documents.

**Sellers** get no password at all, by design. Requiring a stressed homeowner to
create an account at 10pm to fill in a form their agent sent them is how you get
a 40% drop-off before the first question. Instead the seller's credential *is*
the link: a 256-bit URL-safe secret, stored only as a SHA-256 hash, scoped to
exactly one disclosure session, expiring, revocable, and rotatable by the agent.

So: what happens if someone guesses a seller's URL?

They do not. 2^256 is not a guessable space, and the token is compared against a
hash, so a dump of the database does not yield working links either. The honest
risk is not guessing, it is *leakage* - a forwarded text, a shared screen, a
referrer header - because anyone holding the link is treated as the seller. That
is mitigated here by keeping all identifying data out of the URL, sending
`Referrer-Policy: no-referrer`, rate-limiting per token, recording every use,
and letting the agent revoke and reissue in one click.

It is not fully solved, and the README says so plainly: production should put an
emailed one-time code in front of the *signature* step specifically. That is
where the link stops being a convenience and starts being a legal act. It is
listed as knowingly left out rather than quietly missing.
"""

from __future__ import annotations

import hashlib
import hmac
import secrets
from datetime import datetime, timedelta, timezone

from argon2 import PasswordHasher
from argon2.exceptions import VerifyMismatchError
from fastapi import Depends, HTTPException, Request, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from .config import settings
from .db import get_db
from .models import AccessToken, Agent, AgentSession, DisclosureSession, utcnow

_hasher = PasswordHasher()

TOKEN_BYTES = 32  # 256 bits


def hash_password(plain: str) -> str:
    return _hasher.hash(plain)


def verify_password(plain: str, hashed: str) -> bool:
    try:
        _hasher.verify(hashed, plain)
        return True
    except (VerifyMismatchError, Exception):
        return False


def new_token() -> tuple[str, str]:
    """Return (plaintext, sha256 hex). The plaintext is never stored."""
    raw = secrets.token_urlsafe(TOKEN_BYTES)
    return raw, hashlib.sha256(raw.encode()).hexdigest()


def token_hash(raw: str) -> str:
    return hashlib.sha256(raw.encode()).hexdigest()


def _client_ip(request: Request) -> str:
    fwd = request.headers.get("x-forwarded-for", "")
    if fwd:
        return fwd.split(",")[0].strip()[:64]
    return (request.client.host if request.client else "")[:64]


# --------------------------------------------------------------------------
# Agent sessions
# --------------------------------------------------------------------------

def create_agent_session(db: Session, agent: Agent, user_agent: str = "") -> str:
    raw, hashed = new_token()
    db.add(AgentSession(
        agent_id=agent.id,
        token_hash=hashed,
        expires_at=utcnow() + timedelta(hours=settings().session_ttl_hours),
        user_agent=user_agent[:300],
    ))
    db.commit()
    return raw


def revoke_agent_session(db: Session, raw: str) -> None:
    row = db.scalar(select(AgentSession).where(AgentSession.token_hash == token_hash(raw)))
    if row and row.revoked_at is None:
        row.revoked_at = utcnow()
        db.commit()


def _as_aware(dt: datetime | None) -> datetime | None:
    """SQLite drops tzinfo on the way back out; Postgres does not."""
    if dt is None:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def current_agent(request: Request, db: Session = Depends(get_db)) -> Agent:
    raw = request.cookies.get(settings().session_cookie)
    if not raw:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Not signed in")

    row = db.scalar(select(AgentSession).where(AgentSession.token_hash == token_hash(raw)))
    if row is None or row.revoked_at is not None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Session is no longer valid")
    if _as_aware(row.expires_at) < utcnow():
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Session expired")

    agent = db.get(Agent, row.agent_id)
    if agent is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Account no longer exists")
    return agent


# --------------------------------------------------------------------------
# Seller links
# --------------------------------------------------------------------------

def issue_seller_link(db: Session, session_id: str) -> str:
    """Mint a link, revoking any previous one for this disclosure."""
    for old in db.scalars(
        select(AccessToken).where(
            AccessToken.session_id == session_id, AccessToken.revoked_at.is_(None)
        )
    ):
        old.revoked_at = utcnow()

    raw, hashed = new_token()
    db.add(AccessToken(
        session_id=session_id,
        token_hash=hashed,
        expires_at=utcnow() + timedelta(days=settings().seller_link_ttl_days),
    ))
    db.commit()
    return raw


def resolve_seller_token(db: Session, raw: str, request: Request) -> DisclosureSession:
    if not raw or len(raw) < 20:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "This link is not valid")

    row = db.scalar(select(AccessToken).where(AccessToken.token_hash == token_hash(raw)))
    # Same response for wrong, revoked and expired: a distinct "revoked" message
    # would confirm to a stranger that the token was real.
    if row is None or row.revoked_at is not None or _as_aware(row.expires_at) < utcnow():
        raise HTTPException(status.HTTP_404_NOT_FOUND, "This link is not valid or has expired")

    row.use_count += 1
    row.last_used_at = utcnow()
    row.last_ip = _client_ip(request)
    db.commit()

    disclosure = db.get(DisclosureSession, row.session_id)
    if disclosure is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "This disclosure no longer exists")
    return disclosure


def constant_time_eq(a: str, b: str) -> bool:
    return hmac.compare_digest(a, b)
