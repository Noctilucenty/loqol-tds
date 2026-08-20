"""The form model: bindings, coverage, gating, layout."""

from app.tds import gating
from app.tds.bindings import CheckAny, TriCheck, WrappedText, wrap_variable
from app.tds.fieldmap import coverage, resolve, validate, widgets_by_key
from app.tds.questions import QUESTIONS, QUESTIONS_BY_ID


def test_every_binding_addresses_a_real_widget():
    assert validate() == []


def test_every_widget_is_accounted_for():
    """No field on the form is silently ignored."""
    c = coverage()
    assert c["unhandled"] == []
    assert c["widgets_total"] == 159
    assert c["bound_by_questions"] + c["handled_by_signer_roles"] == c["widgets_total"]


def test_solar_collision_is_split():
    """`Solar` owns two widgets on two different lines in the source PDF.

    Checking the pool heater's Solar box must not check the water heater's, which
    is impossible to express through the AcroForm and is the reason bindings
    address widgets rather than field names.
    """
    fields, _ = resolve({"A.pool": True, "A.pool_heater": ["solar"], "A.water_heater": ["gas"]})
    assert fields["Solar"] is True        # pool/spa heater line
    assert fields["Solar#1"] is False     # water heater line
    assert fields["Gas2"] is True         # water heater gas
    assert fields["Gas"] is False         # pool heater gas


def test_unknown_leaves_both_statutory_boxes_clear():
    """'I don't know' is not 'No'. Answering No when unsure creates liability."""
    fields, _ = resolve({"C.flooding": "unknown"})
    assert fields["FloodingYes"] is False
    assert fields["FloodingNo"] is False

    fields, _ = resolve({"C.flooding": "no"})
    assert fields["FloodingNo"] is True


def test_hidden_questions_do_not_print():
    """A pool heater answer must not survive the seller saying there is no pool."""
    answers = {"A.pool": False, "A.hot_tub": False, "A.pool_heater": ["gas"]}
    fields, _ = resolve(answers)
    assert "Gas" not in fields
    assert fields.get("PoolSpaHeater") is None


def test_parent_checkbox_tracks_a_real_choice():
    binding = CheckAny("PoolSpaHeater", ["gas", "solar", "electric"])
    assert list(binding.writes(["none"]))[0].value is False
    assert list(binding.writes(["gas"]))[0].value is True
    assert list(binding.writes([]))[0].value is False


def test_wrapped_text_never_drops_words():
    text = "The roof leaked over the back bedroom in the 2023 storms. " * 8
    w = WrappedText(["IfYesExplain1"] + [f"IfYesExplain{i}" for i in range(2, 6)],
                    widths=[42, 107, 107, 107, 107])
    lines, overflow = w.layout(text)
    seen = " ".join(lines).replace(w.continuation, " ").split() + (overflow or "").split()
    for word in text.split():
        assert word in seen


def test_wrap_variable_handles_a_word_longer_than_the_line():
    lines, left = wrap_variable("supercalifragilistic", [8])
    assert lines[0] == "supercal"
    assert left == ["ifragilistic"]


def test_tri_check_pairs():
    writes = {w.name: w.value for w in TriCheck("Yes1", "No1").writes("yes")}
    assert writes == {"Yes1": True, "No1": False}


def test_gating_grammar():
    a = {"A.garage": True, "A.water_supply": ["city", "private"], "C.flooding": "yes"}
    assert gating.evaluate("A.garage is true", a)
    assert not gating.evaluate("A.garage is false", a)
    assert gating.evaluate("A.water_supply contains private", a)
    assert not gating.evaluate("A.water_supply contains well", a)
    assert gating.evaluate("any C.* is yes", a)
    assert gating.evaluate("A.pool is true or A.garage is true", a)
    assert gating.evaluate(None, a)


def test_every_depends_on_is_parseable_and_references_real_questions():
    for q in QUESTIONS:
        if not q.depends_on:
            continue
        gating.evaluate(q.depends_on, {})  # must not raise
        for ref in gating.referenced_ids(q.depends_on):
            assert ref in QUESTIONS_BY_ID, f"{q.id} depends on unknown {ref}"


def test_every_voice_question_states_what_a_usable_answer_needs():
    """The voice agent is told what to follow up on; a blank here means it won't."""
    for q in QUESTIONS:
        if q.lane == "voice":
            assert q.needs, f"{q.id} is routed to voice with no `needs`"


def test_widget_geometry_converts_to_docuseal_fractions():
    w = widgets_by_key()["KnowledgeYes"]
    area = w.docuseal_area()
    assert area["page"] == 1
    for k in ("x", "y", "w", "h"):
        assert 0.0 <= area[k] <= 1.0
