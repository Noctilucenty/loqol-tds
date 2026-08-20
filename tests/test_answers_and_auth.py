"""Answer semantics, the audit trail, and access control."""

from sqlalchemy import select

from app.models import Answer, AnswerEvent, AnswerSource, AnswerStatus, Flag, FlagState
from app.services import answers_dict, progress, write_answer
from app.tds.rules import evaluate


# ---------------------------------------------------------------- answers ---

def test_answering_twice_keeps_one_row_and_both_events(client, db, seller_link):
    token = seller_link["token"]
    client.put(f"/api/s/{token}/answers", json={"question_id": "C.flooding", "value": "no"})
    client.put(f"/api/s/{token}/answers", json={"question_id": "C.flooding", "value": "yes"})

    sid = db.scalar(select(Answer.session_id).where(Answer.question_id == "C.flooding"))
    rows = db.scalars(
        select(Answer).where(Answer.session_id == sid, Answer.question_id == "C.flooding")
    ).all()
    assert len(rows) == 1, "current value must be a single row"
    assert rows[0].value["v"] == "yes"
    assert rows[0].revision == 2

    events = db.scalars(
        select(AnswerEvent).where(
            AnswerEvent.session_id == sid, AnswerEvent.question_id == "C.flooding"
        )
    ).all()
    assert len(events) == 2, "every write is retained, including the superseded one"
    assert events[1].previous_value["v"] == "no"


def test_a_cross_lane_disagreement_raises_a_flag_instead_of_silently_winning(client, db, seller_link):
    token = seller_link["token"]
    client.put(f"/api/s/{token}/answers", json={
        "question_id": "C.settling", "value": "no", "source": "form",
    })
    sid = db.scalar(select(Answer.session_id).where(Answer.question_id == "C.settling"))

    write_answer(db, sid, "C.settling", "yes", source=AnswerSource.VOICE,
                 transcript="actually there is a crack in the garage slab")

    row = db.scalar(select(Answer).where(
        Answer.session_id == sid, Answer.question_id == "C.settling"))
    assert row.value["v"] == "yes", "the later answer stands"

    flag = db.scalar(select(Flag).where(
        Flag.session_id == sid, Flag.rule_id == "conflict:C.settling"))
    assert flag is not None and flag.state == FlagState.OPEN


def test_unknown_is_distinct_from_no_and_from_unanswered(client, db, seller_link):
    token = seller_link["token"]
    client.put(f"/api/s/{token}/answers", json={
        "question_id": "C.hazards", "value": "unknown", "status": "unknown",
    })
    sid = db.scalar(select(Answer.session_id).where(Answer.question_id == "C.hazards"))
    assert answers_dict(db, sid)["C.hazards"] == "unknown"
    assert "C.encroachments" not in answers_dict(db, sid)


def test_skipped_answers_are_treated_as_unanswered(client, db, seller_link):
    token = seller_link["token"]
    client.put(f"/api/s/{token}/answers", json={
        "question_id": "C.noise", "value": None, "status": "skipped",
    })
    sid = db.scalar(select(Answer.session_id).where(Answer.question_id == "C.noise"))
    assert "C.noise" not in answers_dict(db, sid)


def test_group_commit_never_overwrites_an_existing_answer(client, seller_link):
    token = seller_link["token"]
    client.put(f"/api/s/{token}/answers", json={"question_id": "A.public_sewer", "value": True})
    state = client.post(f"/api/s/{token}/answers/commit-group", json={
        "questionIds": ["A.public_sewer", "A.septic_tank", "A.sump_pump"],
    }).json()
    assert state["answers"]["A.public_sewer"]["value"] is True
    assert state["answers"]["A.septic_tank"]["value"] is False


# ------------------------------------------------------------------ rules ---

def test_sewer_and_septic_is_a_hard_conflict():
    hits = {r.id: r for r in evaluate({"A.public_sewer": True, "A.septic_tank": True})}
    assert "sewer_and_septic" in hits
    assert hits["sewer_and_septic"].severity == "hard"


def test_yes_without_the_required_explanation_is_caught():
    hits = {r.id for r in evaluate({"B.gate": "yes", "B.components": ["roofs"], "B.explain": ""})}
    assert "defects_yes_no_explanation" in hits


def test_a_rule_that_raises_cannot_break_the_session():
    """Rule predicates run against partial answer sets all day long."""
    assert isinstance(evaluate({"A.water_heater": None, "B.components": None}), list)


def test_flags_close_themselves_when_the_contradiction_is_resolved(client, db, seller_link):
    token = seller_link["token"]
    client.put(f"/api/s/{token}/answers", json={"question_id": "A.public_sewer", "value": True})
    client.put(f"/api/s/{token}/answers", json={"question_id": "A.septic_tank", "value": True})
    state = client.get(f"/api/s/{token}/state").json()
    assert any(f["ruleId"] == "sewer_and_septic" for f in state["flags"])

    client.put(f"/api/s/{token}/answers", json={"question_id": "A.septic_tank", "value": False})
    state = client.get(f"/api/s/{token}/state").json()
    assert not any(f["ruleId"] == "sewer_and_septic" for f in state["flags"])


# --------------------------------------------------------------- progress ---

def test_progress_counts_only_reachable_questions():
    empty = progress({})
    assert empty["percent"] == 0
    assert empty["total"] < 100, "progress is over the interview, not over 150 form fields"
    assert empty["minutes_left"] > 0


# ------------------------------------------------------------------- auth ---

def test_a_guessed_seller_url_is_rejected(client):
    r = client.get("/api/s/" + "z" * 43)
    assert r.status_code == 404


def test_a_revoked_link_stops_working_immediately(client, seller_link):
    token = seller_link["token"]
    assert client.get(f"/api/s/{token}/state").status_code == 200
    client.delete(f"/api/agent/deals/{seller_link['deal_id']}/link")
    assert client.get(f"/api/s/{token}/state").status_code == 404


def test_rotating_a_link_invalidates_the_previous_one(client, seller_link):
    old = seller_link["token"]
    new_url = client.post(f"/api/agent/deals/{seller_link['deal_id']}/link").json()["url"]
    new = new_url.rsplit("/", 1)[-1]
    assert new != old
    assert client.get(f"/api/s/{old}/state").status_code == 404
    assert client.get(f"/api/s/{new}/state").status_code == 200


def test_agent_endpoints_require_a_session(client):
    logged_out = client.__class__(client.app)
    assert logged_out.get("/api/agent/deals").status_code == 401


def test_an_agent_cannot_reach_another_agents_deal(client, seller_link):
    other = client.__class__(client.app)
    other.post("/api/auth/register", json={
        "email": "stranger@example.com", "password": "pytest-only-not-a-real-password", "name": "Stranger",
    })
    r = other.get(f"/api/agent/deals/{seller_link['deal_id']}/review")
    assert r.status_code == 404, "404 rather than 403, so deal ids cannot be probed"


def test_the_seller_token_is_not_stored_in_the_clear(client, db, seller_link):
    from app.models import AccessToken

    rows = db.scalars(select(AccessToken)).all()
    assert rows
    assert all(seller_link["token"] not in r.token_hash for r in rows)
    assert all(len(r.token_hash) == 64 for r in rows)
