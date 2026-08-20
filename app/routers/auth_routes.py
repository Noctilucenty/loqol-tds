"""Agent sign-in."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..auth import create_agent_session, current_agent, hash_password, revoke_agent_session, verify_password
from ..config import settings
from ..db import get_db
from ..models import Agent
from ..schemas import AgentOut, LoginIn, RegisterIn

router = APIRouter(prefix="/api/auth", tags=["auth"])


def _set_cookie(response: Response, raw: str) -> None:
    cfg = settings()
    response.set_cookie(
        cfg.session_cookie,
        raw,
        httponly=True,                  # not readable by script, so XSS cannot lift it
        secure=cfg.is_production,       # http on localhost, https everywhere real
        samesite="lax",                 # blocks cross-site POST, keeps normal navigation
        max_age=cfg.session_ttl_hours * 3600,
        path="/",
    )


@router.post("/register", response_model=AgentOut)
def register(body: RegisterIn, response: Response, request: Request, db: Session = Depends(get_db)):
    if db.scalar(select(Agent).where(Agent.email == body.email.lower())):
        raise HTTPException(status.HTTP_409_CONFLICT, "An account with that email already exists")
    agent = Agent(
        email=body.email.lower(),
        password_hash=hash_password(body.password),
        name=body.name,
        brokerage=body.brokerage,
        license_number=body.license_number,
    )
    db.add(agent)
    db.commit()
    _set_cookie(response, create_agent_session(db, agent, request.headers.get("user-agent", "")))
    return AgentOut(id=agent.id, email=agent.email, name=agent.name, brokerage=agent.brokerage)


@router.post("/login", response_model=AgentOut)
def login(body: LoginIn, response: Response, request: Request, db: Session = Depends(get_db)):
    agent = db.scalar(select(Agent).where(Agent.email == body.email.lower()))
    # Verify against a dummy hash when the account is missing so that a wrong
    # email and a wrong password take the same time to fail.
    if agent is None or not verify_password(body.password, agent.password_hash):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Email or password is incorrect")
    _set_cookie(response, create_agent_session(db, agent, request.headers.get("user-agent", "")))
    return AgentOut(id=agent.id, email=agent.email, name=agent.name, brokerage=agent.brokerage)


@router.post("/logout")
def logout(request: Request, response: Response, db: Session = Depends(get_db)):
    raw = request.cookies.get(settings().session_cookie)
    if raw:
        revoke_agent_session(db, raw)
    response.delete_cookie(settings().session_cookie, path="/")
    return {"ok": True}


@router.get("/me", response_model=AgentOut)
def me(agent: Agent = Depends(current_agent)):
    return AgentOut(id=agent.id, email=agent.email, name=agent.name, brokerage=agent.brokerage)
