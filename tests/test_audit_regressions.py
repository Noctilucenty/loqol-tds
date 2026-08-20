"""One test per defect found in review.

Each of these failed before the fix beside it. They are grouped by what the bug
would have done to a real seller, because that is the thing worth not regressing.
"""

import io

import pytest
from pypdf import PdfReader
from sqlalchemy import select

from app.models import (
    Answer, AnswerSource, AnswerStatus, DisclosureSession, Flag, FlagState, SessionStatus,
)
from app.routers.voice_routes import _coerce
from app.services import FrozenDisclosure, answers_dict, settle_flag, sync_flags, write_answer
from app.tds.fieldmap import resolve
from app.tds.fill import render
from app.tds.questions import QUESTIONS_BY_ID
from app.tds.values import ValueError_, coerce

SYSTEM = {"property_address": "1 Test St", "disclosure_date": "2026-08-20"}


# ---------------------------------------------------------------------------
# Wrong marks on a sworn document
# ---------------------------------------------------------------------------

def test_i_dont_know_never_prints_as_a_tick(client, db, seller_link):
    """`bool("unknown")` is True, so the sentinel used to check the box."""
    token = seller_link["token"]
    client.put(f"/api/s/{token}/answers", json={
        "question_id": "A.range", "value": None, "status": "unknown",
    })
    fields, _ = resolve(answers_dict(db, seller_link["session_id"]))
    assert fields.get("Range") in (None, False)


def test_unknown_sentinel_cannot_tick_any_checkbox():
    assert resolve({"A.range": "unknown"})[0]["Range"] is False
    assert resolve({"A.garage": "unknown"})[0]["Garage"] is False


def test_zero_remote_controls_does_not_assert_remotes_transfer():
    """`bool("0")` is True; the form would claim remotes next to a printed 0."""
    for value in (0, "0"):
        fields, _ = resolve({
            "A.garage": True, "A.garage_opener": True, "A.garage_remotes": value,
        })
        assert fields["RemoteControlsYes"] is False, value
        assert fields["NumberRemoteControlsDigit"] == "0"

    fields, _ = resolve({"A.garage": True, "A.garage_opener": True, "A.garage_remotes": 2})
    assert fields["RemoteControlsYes"] is True


def test_an_unreadable_yes_never_becomes_a_no():
    """The worst possible failure: printing No because a phrase was not parsed."""
    tri = QUESTIONS_BY_ID["C.flooding"]
    boolean = QUESTIONS_BY_ID["A.public_sewer"]
    for said in ("Yes, there is a public sewer", "yeah", "Yes.", "yep", 1):
        assert coerce(tri, said, "answered")[0] != "no", said
        assert coerce(boolean, said, "answered")[0] is not False, said
    # And the same through the voice router's wrapper.
    assert _coerce(boolean, "Yes, there is one", "answered")[0] is not False


def test_gibberish_is_recorded_as_unsure_rather_than_denied():
    q = QUESTIONS_BY_ID["A.public_sewer"]
    assert coerce(q, "the thing by the fence", "answered") == (None, "unknown")


# ---------------------------------------------------------------------------
# Writes that should never have been accepted
# ---------------------------------------------------------------------------

def test_a_scalar_on_a_multi_question_cannot_brick_the_session(client, seller_link):
    """This used to commit, then 500 every later read for seller and agent alike."""
    token = seller_link["token"]
    r = client.put(f"/api/s/{token}/answers", json={
        "question_id": "A.water_supply", "value": True,
    })
    assert r.status_code == 200
    assert client.get(f"/api/s/{token}/state").status_code == 200
    assert client.get(f"/api/agent/deals").status_code == 200


def test_the_seller_cannot_answer_the_agents_section(client, seller_link):
    r = client.put(f"/api/s/{seller_link['token']}/answers", json={
        "question_id": "I.multi_unit", "value": True,
    })
    assert r.status_code == 403


def test_the_seller_cannot_answer_a_question_that_is_not_being_asked(client, seller_link):
    token = seller_link["token"]
    client.put(f"/api/s/{token}/answers", json={"question_id": "A.pool", "value": False})
    r = client.put(f"/api/s/{token}/answers", json={
        "question_id": "A.child_barrier", "value": True,
    })
    assert r.status_code == 409


def test_a_client_cannot_label_its_own_lane(client, db, seller_link):
    """Claiming source=agent corrupted the audit trail and silenced conflicts."""
    token = seller_link["token"]
    client.put(f"/api/s/{token}/answers", json={
        "question_id": "C.noise", "value": "no", "source": "agent",
    })
    row = db.scalar(select(Answer).where(Answer.question_id == "C.noise"))
    assert row.source == AnswerSource.FORM


def test_group_commit_only_touches_plain_yes_no_tiles(client, db, seller_link):
    """False into a statutory Yes/No pair blanks both boxes but counts answered."""
    token = seller_link["token"]
    client.post(f"/api/s/{token}/answers/commit-group", json={
        "questionIds": ["B.gate", "I.substituted", "A.range"],
    })
    stored = answers_dict(db, seller_link["session_id"])
    assert stored.get("A.range") is False       # a real inventory tile
    assert "B.gate" not in stored               # statutory pair, left alone
    assert "I.substituted" not in stored        # the agent's question


def test_a_negative_count_is_rejected():
    with pytest.raises(ValueError_):
        coerce(QUESTIONS_BY_ID["A.garage_remotes"], -3, "answered")


# ---------------------------------------------------------------------------
# The document is frozen once it is sent
# ---------------------------------------------------------------------------

def test_no_lane_can_edit_a_disclosure_that_was_sent_for_signature(client, db, seller_link):
    token = seller_link["token"]
    sid = seller_link["session_id"]
    client.put(f"/api/s/{token}/answers", json={"question_id": "C.noise", "value": "no"})

    ds = db.get(DisclosureSession, sid)
    ds.status = SessionStatus.SENT_FOR_SIGNATURE
    db.commit()

    assert client.put(f"/api/s/{token}/answers", json={
        "question_id": "C.noise", "value": "yes"}).status_code == 409
    assert client.post(f"/api/voice/{token}/answer", json={
        "question_id": "C.noise", "value": "yes", "status": "answered"}).status_code == 409
    assert client.post(f"/api/s/{token}/answers/commit-group", json={
        "questionIds": ["A.oven"]}).status_code == 409

    with pytest.raises(FrozenDisclosure):
        write_answer(db, sid, "C.noise", "yes")

    ds.status = SessionStatus.IN_PROGRESS
    db.commit()


# ---------------------------------------------------------------------------
# Decisions a human made have to stick
# ---------------------------------------------------------------------------

def test_a_dismissed_flag_is_not_resurrected_by_the_next_write(client, db, seller_link):
    """Otherwise a dismissed hard flag deadlocks the deal forever."""
    token = seller_link["token"]
    sid = seller_link["session_id"]
    client.put(f"/api/s/{token}/answers", json={"question_id": "A.public_sewer", "value": True})
    client.put(f"/api/s/{token}/answers", json={"question_id": "A.septic_tank", "value": True})

    flag = db.scalar(select(Flag).where(
        Flag.session_id == sid, Flag.rule_id == "sewer_and_septic", Flag.state == FlagState.OPEN))
    assert flag is not None
    settle_flag(db, flag, state=FlagState.DISMISSED, note="rural parcel, both are real", by="agent")

    client.put(f"/api/s/{token}/answers", json={"question_id": "A.oven", "value": True})

    rows = db.scalars(select(Flag).where(
        Flag.session_id == sid, Flag.rule_id == "sewer_and_septic")).all()
    assert len(rows) == 1
    assert rows[0].state == FlagState.DISMISSED


def test_a_settled_decision_lapses_when_its_answers_change(client, db, seller_link):
    """The decision is about a specific set of answers, not about the rule.

    While those answers hold, the decision holds - a rural parcel really can have
    both a sewer and a septic tank, and nobody should have to re-dismiss it. If
    the answers behind it move, the decision no longer applies to what the form
    now says, so the flag is raised again.
    """
    token, sid = seller_link["token"], seller_link["session_id"]
    client.put(f"/api/s/{token}/answers", json={"question_id": "C.hoa", "value": "yes"})
    client.put(f"/api/s/{token}/answers", json={"question_id": "C.ccrs", "value": "no"})

    flag = db.scalar(select(Flag).where(
        Flag.session_id == sid, Flag.rule_id == "hoa_without_ccrs", Flag.state == FlagState.OPEN))
    assert flag is not None
    settle_flag(db, flag, state=FlagState.RESOLVED, note="checked with the title company")

    # Same answers: the decision stands.
    sync_flags(db, sid)
    assert not db.scalars(select(Flag).where(
        Flag.session_id == sid, Flag.rule_id == "hoa_without_ccrs",
        Flag.state == FlagState.OPEN)).all()

    # Different answers behind the same rule: the decision no longer applies.
    flag.resolution = {**(flag.resolution or {}), "fingerprint": "answers that are no longer true"}
    db.commit()
    sync_flags(db, sid)
    assert db.scalars(select(Flag).where(
        Flag.session_id == sid, Flag.rule_id == "hoa_without_ccrs",
        Flag.state == FlagState.OPEN)).all()


# ---------------------------------------------------------------------------
# Signature handling
# ---------------------------------------------------------------------------

def test_send_for_signature_refuses_to_ship_a_truncated_disclosure(client, seller_link):
    """The template has three fixed pages and nowhere to put a continuation sheet."""
    token = seller_link["token"]
    long = "The roof leaked over the back bedroom in the 2023 storms. " * 12
    client.put(f"/api/s/{token}/answers", json={"question_id": "B.gate", "value": "yes"})
    client.put(f"/api/s/{token}/answers", json={"question_id": "B.components", "value": ["roofs"]})
    client.put(f"/api/s/{token}/answers", json={"question_id": "B.explain", "value": long})

    r = client.post(f"/api/agent/deals/{seller_link['deal_id']}/send-for-signature")
    assert r.status_code in (409, 503)
    if r.status_code == 409:
        detail = r.json()["detail"]
        assert isinstance(detail, dict)


def test_the_local_preview_still_carries_the_addendum(client, seller_link):
    long = "The roof leaked over the back bedroom in the 2023 storms. " * 12
    _, overflow = resolve({"B.gate": "yes", "B.components": ["roofs"], "B.explain": long})
    assert overflow
    fields, _ = resolve({"B.gate": "yes", "B.components": ["roofs"], "B.explain": long})
    pdf = render(fields, system=SYSTEM, overflow=overflow)
    assert len(PdfReader(io.BytesIO(pdf)).pages) > 3


# ---------------------------------------------------------------------------
# Access control
# ---------------------------------------------------------------------------

def test_the_agent_form_spec_requires_a_session(client):
    anon = client.__class__(client.app)
    assert anon.get("/api/agent/form-spec").status_code == 401


def test_the_demo_account_is_isolated_per_visitor(client):
    a = client.__class__(client.app)
    b = client.__class__(client.app)
    ra, rb = a.post("/api/auth/demo"), b.post("/api/auth/demo")
    assert ra.status_code == rb.status_code == 200
    assert ra.json()["id"] != rb.json()["id"]
    assert ra.json()["email"] != rb.json()["email"]

    a.post("/api/agent/deals", json={
        "property_address": "1 A St", "seller_name": "A", "seller_email": "a@example.com"})
    assert len(b.get("/api/agent/deals").json()) == 1  # only its own seeded deal


# ---------------------------------------------------------------------------
# The rendered document
# ---------------------------------------------------------------------------

def test_flattening_removes_the_widgets_not_just_the_catalog_entry():
    fields, _ = resolve({"A.range": True})
    pdf = render(fields, system=SYSTEM)
    assert b"/Widget" not in pdf
    assert len(pdf) < 450_000, "orphaned form objects were left behind"


# ---------------------------------------------------------------------------
# What the realtime model actually sends back
# ---------------------------------------------------------------------------

def test_a_spoken_sentence_is_read_as_the_answer_it_obviously_is():
    """The model returns prose even when the schema asks for one word.

    Observed live: asked "any environmental hazards?", it answered with
    `value="No hazards like asbestos, lead paint... have ever turned up"`. That
    is unambiguously a no. Filing it as unknown would silently drop a clear
    answer the seller gave out loud.
    """
    q = QUESTIONS_BY_ID["C.hazards"]
    assert coerce(q, "No hazards like asbestos or lead paint have ever turned up.", "answered") \
        == ("no", "answered")
    assert coerce(q, "Yes, actually. We share the back fence with the neighbour.", "answered") \
        == ("yes", "answered")
    assert coerce(q, "Nothing like that has ever come up.", "answered") == ("no", "answered")


def test_polarity_is_only_read_off_the_front_of_the_sentence():
    """"The neighbour said no when we asked" is not the seller saying no."""
    q = QUESTIONS_BY_ID["C.hazards"]
    assert coerce(q, "The neighbour said no when we asked", "answered")[1] == "unknown"


def test_a_hedged_sentence_stays_unsure_however_it_ends():
    q = QUESTIONS_BY_ID["C.encroachments"]
    assert coerce(q, "I'm not sure whether the shed crosses the line", "answered") \
        == ("unknown", "unknown")


# ---------------------------------------------------------------------------
# Second review round: regressions my own fixes introduced
# ---------------------------------------------------------------------------

def test_a_street_name_starting_with_a_hedge_word_is_not_an_unknown():
    """"Maybell Ave" starts with "maybe"; prefix matching needs a word boundary."""
    q = QUESTIONS_BY_ID["P.address"]
    assert coerce(q, "Maybell Ave 3400, Palo Alto, CA", "answered") \
        == ("Maybell Ave 3400, Palo Alto, CA", "answered")
    assert coerce(q, "Nolan St 12", "answered")[1] == "answered"


def test_a_hedge_in_free_text_is_the_answer_not_an_unknown():
    """In a narrative, "not sure which winter" is content, not an abstention."""
    q = QUESTIONS_BY_ID["C.explain"]
    value, status = coerce(q, "Not sure which winter it was, maybe 2021.", "answered")
    assert status == "answered"
    assert "2021" in value


def test_a_yes_no_hedge_still_becomes_unknown():
    q = QUESTIONS_BY_ID["C.flooding"]
    assert coerce(q, "maybe", "answered") == ("unknown", "unknown")
    assert coerce(q, "not sure about the shed", "answered") == ("unknown", "unknown")


def test_creating_a_deal_leaves_it_not_started(client):
    """Seeding an answer flipped every new deal to "in progress" before the
    seller had opened the link, which is the one thing that column tells you."""
    fresh = client.__class__(client.app)
    fresh.post("/api/auth/demo")
    deal = fresh.post("/api/agent/deals", json={
        "property_address": "9 Fresh St", "seller_name": "Pat", "seller_email": "p@example.com",
    }).json()
    assert deal["status"] == "draft"
    assert deal["percent"] == 0


def test_a_first_time_seller_sees_a_fresh_disclosure(client, seller_link):
    """Nothing is answered before the seller touches it, so the welcome screen
    (which is gated on zero answers) is reachable."""
    state = client.get(f"/api/s/{seller_link['token']}").json()
    assert state["progress"]["answered"] == 0


def test_confirming_the_address_asks_for_nothing_more(client, seller_link):
    """It is a yes/no question, so Yes should cost one tap and open no input."""
    token = seller_link["token"]
    r = client.put(f"/api/s/{token}/answers", json={"question_id": "P.address_ok", "value": True})
    assert r.status_code == 200
    assert "P.address" not in r.json()["missingRequired"]


def test_correcting_the_address_reaches_the_printed_form(client, seller_link):
    """The address is deal metadata; without a write-back the seller corrects it,
    is told it prints on all three pages, and it does not."""
    token = seller_link["token"]
    client.put(f"/api/s/{token}/answers", json={"question_id": "P.address_ok", "value": False})
    client.put(f"/api/s/{token}/answers", json={
        "question_id": "P.address", "value": "999 Corrected Ave, Culver City, CA 90230",
    })
    review = client.get(f"/api/agent/deals/{seller_link['deal_id']}/review").json()
    assert review["deal"]["property_address"] == "999 Corrected Ave, Culver City, CA 90230"


def test_the_correction_box_is_hidden_when_the_address_is_confirmed(client, seller_link):
    """Answering a question that is not being asked must still be refused."""
    token = seller_link["token"]
    client.put(f"/api/s/{token}/answers", json={"question_id": "P.address_ok", "value": True})
    r = client.put(f"/api/s/{token}/answers", json={
        "question_id": "P.address", "value": "should not be accepted"})
    assert r.status_code == 409


def test_a_hard_flag_cannot_be_closed_by_re_confirming_the_same_answer(client, db, seller_link):
    """Re-tapping the value you already had is a no-op write. Settling on it
    stamped a fingerprint over unchanged answers and suppressed the rule for
    good, so the form shipped with the contradiction and the gate defeated."""
    token, sid = seller_link["token"], seller_link["session_id"]
    client.put(f"/api/s/{token}/answers", json={"question_id": "B.gate", "value": "yes"})
    client.put(f"/api/s/{token}/answers", json={"question_id": "B.components", "value": []})

    flag = db.scalar(select(Flag).where(
        Flag.session_id == sid, Flag.rule_id == "defects_yes_no_components",
        Flag.state == FlagState.OPEN))
    assert flag is not None

    # Re-affirm the value that caused it.
    client.post(f"/api/s/{token}/flags/{flag.id}/resolve", json={
        "questionId": "B.gate", "value": "yes"})

    db.expire_all()
    assert db.scalar(select(Flag).where(Flag.id == flag.id)).state == FlagState.OPEN
    assert client.post(f"/api/s/{token}/submit").json()["ok"] is False


def test_an_agent_only_contradiction_does_not_trap_the_seller(client, db, seller_link):
    """`substituted_disclosures_conflict` names only Section I. The seller has no
    control for it and no dismiss button, so it must not block their submit."""
    token, sid = seller_link["token"], seller_link["session_id"]
    write_answer(db, sid, "I.substituted", ["none", "inspection_reports"],
                 source=AnswerSource.AGENT, actor="agent", advance_cursor=False)
    open_rules = {f.rule_id for f in sync_flags(db, sid)}
    assert "substituted_disclosures_conflict" in open_rules

    result = client.post(f"/api/s/{token}/submit").json()
    blocking = {f["message"] for f in result.get("hardFlags", [])}
    assert not any("substituted" in m.lower() for m in blocking), blocking


def test_a_frozen_disclosure_refuses_writes_that_would_change_nothing(client, db, seller_link):
    """The guard must key off the disclosure's state, not off whether a write
    happened to be needed. A group commit whose tiles are all answered writes
    nothing, and used to return 200 against a document already out for
    signature."""
    token, sid = seller_link["token"], seller_link["session_id"]
    client.put(f"/api/s/{token}/answers", json={"question_id": "A.oven", "value": True})

    ds = db.get(DisclosureSession, sid)
    ds.status = SessionStatus.SENT_FOR_SIGNATURE
    db.commit()

    # A.oven already has an answer, so this commit would write nothing at all.
    r = client.post(f"/api/s/{token}/answers/commit-group", json={"questionIds": ["A.oven"]})
    assert r.status_code == 409
    assert client.post(f"/api/s/{token}/submit").status_code == 409

    ds.status = SessionStatus.IN_PROGRESS
    db.commit()


def test_dates_print_in_us_format():
    """The form is a California legal instrument; ISO dates read as machine output."""
    from datetime import date
    from app.tds.fill import us_date
    assert us_date(date(2026, 8, 20)) == "08/20/2026"
