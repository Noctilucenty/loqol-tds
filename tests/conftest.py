import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

# A file-backed SQLite database per test session, configured before app import
# so the engine picks it up.
_tmp = tempfile.mkdtemp(prefix="loqol-test-")
os.environ["DATABASE_URL"] = f"sqlite:///{_tmp}/test.db"
os.environ["ENVIRONMENT"] = "test"
# Explicitly blank rather than unset: pydantic-settings still reads the real
# .env file, and environment variables take priority over it. Popping would let
# a developer's live keys leak into the test run.
os.environ["DOCUSEAL_API_KEY"] = ""
os.environ["OPENAI_API_KEY"] = ""
os.environ["DOCUSEAL_TEMPLATE_ID"] = ""

import pytest
from fastapi.testclient import TestClient

from app.db import SessionLocal, init_db
from app.main import app


@pytest.fixture(scope="session")
def client():
    with TestClient(app) as c:
        yield c


@pytest.fixture
def db():
    init_db()
    s = SessionLocal()
    try:
        yield s
    finally:
        s.close()


@pytest.fixture
def seller_link(client):
    """An agent, a deal and a fresh seller link token."""
    import uuid

    email = f"agent-{uuid.uuid4().hex[:8]}@loqol.ai"
    client.post("/api/auth/register", json={
        "email": email, "password": "pytest-only-not-a-real-password", "name": "Alex Marchetti",
    })
    deal = client.post("/api/agent/deals", json={
        "property_address": "1247 Sepulveda Blvd, Culver City, CA 90230",
        "city": "Culver City", "county": "Los Angeles",
        "seller_name": "Dana Whitfield", "seller_email": "dana@example.com",
    }).json()
    url = client.post(f"/api/agent/deals/{deal['id']}/link").json()["url"]
    # The session id matters: the database persists across tests, so a query by
    # question_id alone would match rows another test wrote.
    session_id = client.get(f"/api/agent/deals/{deal['id']}/review").json()["deal"]["session_id"]
    return {
        "deal_id": deal["id"],
        "token": url.rsplit("/", 1)[-1],
        "session_id": session_id,
        "client": client,
    }
