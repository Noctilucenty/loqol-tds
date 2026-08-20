"""The voice lane's trust boundary, and the rendered document."""

import io

from pypdf import PdfReader

from app.routers.voice_routes import _coerce, build_instructions, build_tools
from app.tds.fieldmap import resolve
from app.tds.fill import render
from app.tds.questions import QUESTIONS_BY_ID


# ------------------------------------------------------------------ voice ---

def test_a_model_cannot_invent_a_question_id(client, seller_link):
    r = client.post(f"/api/voice/{seller_link['token']}/answer", json={
        "question_id": "C.definitely_not_real", "value": "yes", "status": "answered",
    })
    assert r.status_code == 400


def test_a_model_cannot_answer_a_question_that_is_not_being_asked(client, seller_link):
    """`A.child_barrier` is gated behind there being a pool."""
    token = seller_link["token"]
    client.put(f"/api/s/{token}/answers", json={"question_id": "A.pool", "value": False})
    r = client.post(f"/api/voice/{token}/answer", json={
        "question_id": "A.child_barrier", "value": True, "status": "answered",
    })
    assert r.status_code == 409


def test_voice_answers_land_in_the_same_store_as_tapped_ones(client, seller_link):
    token = seller_link["token"]
    r = client.post(f"/api/voice/{token}/answer", json={
        "question_id": "C.major_damage", "value": "yes", "status": "answered",
        "transcript": "there was a kitchen fire in 2019",
    })
    assert r.status_code == 200
    state = client.get(f"/api/s/{token}/state").json()
    assert state["answers"]["C.major_damage"]["value"] == "yes"
    assert state["answers"]["C.major_damage"]["source"] == "voice"


def test_hedged_speech_becomes_unknown_not_no():
    q = QUESTIONS_BY_ID["C.flooding"]
    for said in ("not sure", "maybe", "I don't know", "dont know"):
        assert _coerce(q, said, "answered") == ("unknown", "unknown")
    assert _coerce(q, "yes", "answered") == ("yes", "answered")
    assert _coerce(q, True, "answered") == ("yes", "answered")
    assert _coerce(q, "no", "answered") == ("no", "answered")


def test_invalid_options_are_dropped_rather_than_stored():
    q = QUESTIONS_BY_ID["A.water_heater"]
    assert _coerce(q, ["gas", "plutonium"], "answered") == (["gas"], "answered")


def test_a_non_numeric_count_does_not_become_zero():
    q = QUESTIONS_BY_ID["A.garage_remotes"]
    assert _coerce(q, "a couple", "answered") == (None, "unknown")
    assert _coerce(q, "3", "answered") == (3, "answered")


def test_the_tool_schema_only_offers_currently_visible_questions():
    tools = build_tools({"A.pool": False})
    ids = tools[0]["parameters"]["properties"]["question_id"]["enum"]
    assert "C.hazards" in ids
    assert all(i in QUESTIONS_BY_ID for i in ids)


def test_the_agent_brief_carries_the_follow_ups_it_must_ask_for(db):
    from app.models import Deal

    deal = Deal(agent_id="x", property_address="1 Test St", seller_name="Dana",
                seller_email="d@example.com")
    brief = build_instructions(deal, {})
    assert "1 Test St" in brief
    assert "unknown" in brief
    assert "a usable answer needs" in brief


def test_voice_is_off_and_says_so_when_unconfigured(client, seller_link):
    cfg = client.get(f"/api/voice/{seller_link['token']}/config").json()
    assert cfg["enabled"] is False
    r = client.post(f"/api/voice/{seller_link['token']}/session")
    assert r.status_code == 503
    assert "tapping" in r.json()["detail"]


# -------------------------------------------------------------------- pdf ---

SYSTEM = {"property_address": "1247 Sepulveda Blvd", "disclosure_date": "2026-08-20"}


def test_the_rendered_pdf_is_flat():
    """The output is a record, not a form. No viewer should offer to fill it.

    `get_fields()` alone is not a real check - it reads the document catalog, so
    deleting /AcroForm makes it pass while every widget dictionary is still in
    the file. The byte-level assertion is the one that can actually fail.
    """
    fields, _ = resolve({"A.range": True, "P.occupying": "is"})
    pdf = render(fields, system=SYSTEM)
    reader = PdfReader(io.BytesIO(pdf))

    assert reader.get_fields() in (None, {})
    assert "/AcroForm" not in reader.trailer["/Root"]
    assert not any("/Annots" in page for page in reader.pages)
    assert b"/Widget" not in pdf, "orphaned widget dictionaries survived the flatten"
    assert len(reader.pages) == 3


def test_overflow_is_carried_onto_an_addendum_page():
    long = "The roof leaked over the back bedroom in the 2023 storms. " * 10
    answers = {"B.gate": "yes", "B.components": ["roofs"], "B.explain": long}
    fields, overflow = resolve(answers)
    assert overflow, "a long explanation must not be silently truncated"
    pdf = render(fields, system=SYSTEM, overflow=overflow)
    reader = PdfReader(io.BytesIO(pdf))
    assert len(reader.pages) > 3
    assert "ADDENDUM" in reader.pages[3].extract_text()


def test_the_addendum_restates_the_answer_in_full():
    """A continuation sheet that opens mid-sentence is not a usable disclosure."""
    long = "Zebra " + ("the roof leaked in the 2023 storms " * 12)
    _, overflow = resolve({"B.gate": "yes", "B.components": ["roofs"], "B.explain": long})
    assert overflow[0].split("\n", 1)[1].startswith("Zebra")


def test_a_filled_form_reaches_the_page_it_should():
    answers = {"P.occupying": "is", "A.range": True, "C.flooding": "yes"}
    fields, _ = resolve(answers)
    text = PdfReader(io.BytesIO(render(fields, system=SYSTEM))).pages[1].extract_text()
    assert "1247 Sepulveda Blvd" in text
