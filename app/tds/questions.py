"""The TDS as a seller answers it, not as it is printed.

The printed form is organised for a title officer. It opens with a section about
which inspection reports will be attached to the transfer - a question no
homeowner can answer - and it buries the two questions that actually carry legal
risk on page two in nine-point type.

This module re-cuts the same 150-odd fields into an interview: chapters in the
order a person can actually answer them, each question written in plain English
with the statutory wording available on demand, and each one routed to voice or
to tap with a recorded reason.

Nothing here knows about HTTP, the database, or DocuSeal.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal

from .bindings import Binding, Check, CheckAny, CheckOption, SingleCheck, Text, TriCheck, WrappedText
from .routing import Lane, Why

Kind = Literal["bool", "tri", "multi", "single", "text", "longtext", "int", "date"]


@dataclass(frozen=True)
class Option:
    id: str
    label: str


@dataclass
class Chapter:
    id: str
    title: str
    blurb: str
    #: Rough minutes, used to tell the seller how much is left in human units.
    minutes: float
    #: Chapters the seller never sees. Section I is the agent's job.
    audience: Literal["seller", "agent"] = "seller"


@dataclass
class Question:
    id: str
    chapter: str
    prompt: str
    kind: Kind
    lane: Lane
    why: Why
    #: The statutory wording, shown on demand. Sellers sign this, so it stays reachable.
    legal: str = ""
    #: Plain-English gloss shown when the seller taps "what does this mean?".
    explain: str = ""
    #: A concrete instance, which is what actually unsticks people.
    example: str = ""
    options: list[Option] = field(default_factory=list)
    bindings: list[Binding] = field(default_factory=list)
    #: Question is only asked when this predicate over current answers holds.
    depends_on: str | None = None
    group: str = ""
    #: True for questions whose answer the form requires if the gate opened.
    required_when_shown: bool = True
    #: Voice agent hint: what a usable answer must contain.
    needs: str = ""

    @property
    def allows_unknown(self) -> bool:
        return self.kind == "tri"


# --------------------------------------------------------------------------
# Chapters
# --------------------------------------------------------------------------

CHAPTERS: list[Chapter] = [
    Chapter("coordination", "Disclosure coordination", "Which reports travel with this transfer.", 1.0, audience="agent"),
    Chapter("place", "Your property", "Confirming the basics before we start.", 1.0),
    Chapter("inside", "Inside the home", "Appliances, heating, alarms.", 2.0),
    Chapter("systems", "Water, gas and sewer", "How the house is supplied.", 1.5),
    Chapter("outside", "Outside and parking", "Yard, garage, roof.", 1.5),
    Chapter("pool", "Pool and spa", "Only if you have one.", 0.5),
    Chapter("working", "Anything not working", "One question, then a short description.", 1.5),
    Chapter("defects", "Known defects", "The parts of the house with problems.", 2.0),
    Chapter("awareness", "What you know about the property", "The sixteen legal questions, one at a time.", 6.0),
    Chapter("review", "Review and sign", "Check it over, then sign.", 2.0),
]

CHAPTERS_BY_ID = {c.id: c for c in CHAPTERS}


# --------------------------------------------------------------------------
# Helpers for the long inventory
# --------------------------------------------------------------------------

def item(qid: str, chapter: str, group: str, label: str, field_name: str, *, occurrence: int = 0) -> Question:
    """A Section A inventory checkbox: 'does the property have X?'."""
    return Question(
        id=qid,
        chapter=chapter,
        group=group,
        prompt=label,
        kind="bool",
        lane=Lane.TAP,
        why=Why.ENUMERATION,
        legal=label,
        bindings=[Check(field_name, occurrence=occurrence)],
    )


def inventory(chapter: str, group: str, rows: list[tuple[str, str, str]]) -> list[Question]:
    return [item(qid, chapter, group, label, name) for qid, label, name in rows]


def aware(qid: str, number: int, prompt: str, explain: str, example: str, yes: str, no: str, legal: str) -> Question:
    """One of Section C's sixteen 'are you aware of' questions."""
    return Question(
        id=qid,
        chapter="awareness",
        group=f"C{number}",
        prompt=prompt,
        kind="tri",
        lane=Lane.VOICE,
        why=Why.COMPREHENSION,
        legal=legal,
        explain=explain,
        example=example,
        bindings=[TriCheck(yes, no)],
        needs="A yes, a no, or an explicit 'I don't know'. If yes, also what happened, roughly when, and whether it was fixed.",
    )


# --------------------------------------------------------------------------
# The questions
# --------------------------------------------------------------------------

QUESTIONS: list[Question] = []

# -- Agent-owned: Section I ------------------------------------------------
# Deliberately not in the seller flow. See routing.Why.AGENT_OWNED.
QUESTIONS += [
    Question(
        id="I.substituted",
        chapter="coordination",
        prompt="Which substituted disclosures travel with this transfer?",
        kind="multi",
        lane=Lane.TAP,
        why=Why.AGENT_OWNED,
        legal=(
            "Inspection reports conducted in accordance with the terms of the sales contract "
            "or deposit receipt / Additional inspection reports or disclosures / No substituted "
            "disclosures for this transfer."
        ),
        explain="Set by the listing agent. The seller is never asked this.",
        options=[
            Option("inspection_reports", "Inspection reports per the sales contract"),
            Option("additional", "Additional inspection reports or disclosures"),
            Option("none", "No substituted disclosures for this transfer"),
        ],
        bindings=[
            CheckOption("InspectionReportsYes", "inspection_reports"),
            CheckOption("AdditionalInspectionReportsYes", "additional"),
            CheckOption("NoSubstitutedDisclosures", "none"),
        ],
    ),
    Question(
        id="I.additional_list",
        chapter="coordination",
        prompt="List the additional reports or disclosures.",
        kind="text",
        lane=Lane.TAP,
        why=Why.AGENT_OWNED,
        depends_on="I.substituted contains additional",
        bindings=[Text("ListAdditionalInspectionReports")],
    ),
    Question(
        id="I.multi_unit",
        chapter="coordination",
        prompt="Is this property a duplex, triplex or fourplex?",
        kind="bool",
        lane=Lane.TAP,
        why=Why.AGENT_OWNED,
        legal="This property is a duplex, triplex or fourplex. A TDS is required for all units.",
        bindings=[Check("PropertyTypeDuplexTriplexFourplex")],
    ),
    Question(
        id="I.units_covered",
        chapter="coordination",
        prompt="Does this TDS cover only specific unit(s)?",
        kind="text",
        lane=Lane.TAP,
        why=Why.AGENT_OWNED,
        depends_on="I.multi_unit is true",
        legal="This TDS is for all units (or only unit(s) ____).",
        bindings=[Check("OnlyUnits"), Text("UnitsNumber")],
    ),
]

# -- Chapter: your property ------------------------------------------------
QUESTIONS += [
    Question(
        id="P.address_ok",
        chapter="place",
        prompt="Is this the right property address?",
        kind="bool",
        lane=Lane.TAP,
        why=Why.PRECISION,
        explain=(
            "Your agent entered this. It prints on all three pages of the form, so a "
            "wrong digit is worth catching now."
        ),
        # Deliberately unbound. Confirming changes nothing; only a correction does,
        # and that is the question below.
        bindings=[],
    ),
    Question(
        id="P.address",
        chapter="place",
        prompt="What is the correct address?",
        kind="text",
        lane=Lane.TAP,
        why=Why.PRECISION,
        depends_on="P.address_ok is false",
        explain="Write it as it appears on the deed, including the unit number if there is one.",
        # Unbound for the same reason as ever: the address is deal metadata and
        # reaches all three page headers through roles.SYSTEM_FIELDS. Answering
        # this updates the deal record itself.
        bindings=[],
    ),
    Question(
        id="P.occupying",
        chapter="place",
        prompt="Do you currently live in this home?",
        kind="single",
        lane=Lane.TAP,
        why=Why.GATE,
        legal="Seller [ ] is [ ] is not occupying the property.",
        explain=(
            "This matters more than it looks. If you do not live there, buyers read every "
            "answer that follows as second-hand, and nobody expects you to know how the "
            "upstairs shower behaves."
        ),
        options=[Option("is", "Yes, I live here"), Option("is_not", "No, I do not live here")],
        bindings=[SingleCheck({"is": "SellerIsOccupying", "is_not": "SellerNotOccupying"})],
    ),
]

# -- Chapter: inside the home ---------------------------------------------
QUESTIONS += inventory("inside", "Kitchen and laundry", [
    ("A.range", "Range", "Range"),
    ("A.oven", "Oven", "Oven"),
    ("A.microwave", "Microwave", "Microwave"),
    ("A.dishwasher", "Dishwasher", "Dishwasher"),
    ("A.trash_compactor", "Trash Compactor", "TrashCompactor"),
    ("A.garbage_disposal", "Garbage Disposal", "GarbageDisposal"),
    ("A.washer_dryer_hookups", "Washer/Dryer Hookups", "WasherDryerHookups"),
])
QUESTIONS += inventory("inside", "Heating and cooling", [
    ("A.central_heating", "Central Heating", "CentralHeating"),
    ("A.central_ac", "Central Air Conditioning", "CentralAirConditioning"),
    ("A.evaporator_cooler", "Evaporator Cooler(s)", "EvaporatorCoolers"),
    ("A.wall_window_ac", "Wall/Window Air Conditioning", "WallWindowAirConditioning"),
])
QUESTIONS += inventory("inside", "Safety and security", [
    ("A.burglar_alarms", "Burglar Alarms", "BurglarAlarms"),
    ("A.carbon_monoxide", "Carbon Monoxide Device(s)", "CarbonMonoxideDevices"),
    ("A.smoke_detectors", "Smoke Detector(s)", "SmokeDetectors"),
    ("A.fire_alarm", "Fire Alarm", "FireAlarm"),
    ("A.security_gates", "Security Gate(s)", "SecurityGates"),
    ("A.window_security_bars", "Window Security Bars", "WindowSecurityBars"),
    ("A.quick_release", "Quick Release Mechanism on Bedroom Windows", "QuickReleaseWindows"),
    ("A.window_screens", "Window Screens", "WindowScreens"),
])
QUESTIONS += inventory("inside", "Media and comms", [
    ("A.tv_antenna", "TV Antenna", "TVAntenna"),
    ("A.satellite_dish", "Satellite Dish", "SatelliteDish"),
    ("A.intercom", "Intercom", "Intercom"),
])

# -- Chapter: water, gas and sewer ----------------------------------------
QUESTIONS += inventory("systems", "Sewer and water treatment", [
    ("A.public_sewer", "Public Sewer System", "PublicSewerSystem"),
    ("A.septic_tank", "Septic Tank", "SepticTank"),
    ("A.sump_pump", "Sump Pump", "SumpPump"),
    ("A.water_softener", "Water Softener", "WaterSoftener"),
    ("A.water_conserving", "Water-Conserving Plumbing Fixtures", "WaterConservingPlumbingFixtures"),
])
QUESTIONS += [
    Question(
        id="A.water_heater",
        chapter="systems",
        group="Supply",
        prompt="What does your water heater run on?",
        kind="multi",
        lane=Lane.TAP,
        why=Why.COMPOUND,
        legal="Water Heater: [ ] Gas [ ] Solar [ ] Electric",
        explain=(
            "One question, not three. Pick every fuel that applies - a solar system with a "
            "gas backup is common and both boxes belong checked."
        ),
        options=[Option("gas", "Gas"), Option("solar", "Solar"), Option("electric", "Electric")],
        # NOTE: the second `Solar` widget. See bindings module for the collision.
        bindings=[
            CheckOption("Gas2", "gas"),
            CheckOption("Solar", "solar", occurrence=1),
            CheckOption("Electric2", "electric"),
        ],
    ),
    Question(
        id="A.water_supply",
        chapter="systems",
        group="Supply",
        prompt="Where does your water come from?",
        kind="multi",
        lane=Lane.TAP,
        why=Why.COMPOUND,
        legal="Water Supply: [ ] City [ ] Well [ ] Private Utility or Other ____",
        options=[
            Option("city", "City"),
            Option("well", "Well"),
            Option("private", "Private utility or other"),
        ],
        bindings=[
            CheckOption("City", "city"),
            CheckOption("Well", "well"),
            CheckOption("PrivateUtility", "private"),
        ],
    ),
    Question(
        id="A.water_supply_other",
        chapter="systems",
        group="Supply",
        prompt="Who supplies it?",
        kind="text",
        lane=Lane.TAP,
        why=Why.PRECISION,
        depends_on="A.water_supply contains private",
        bindings=[Text("UtilityOther")],
    ),
    Question(
        id="A.gas_supply",
        chapter="systems",
        group="Supply",
        prompt="How is gas supplied to the house?",
        kind="multi",
        lane=Lane.TAP,
        why=Why.COMPOUND,
        legal="Gas Supply: [ ] Utility [ ] Bottled (Tank)",
        options=[Option("utility", "Utility"), Option("bottled", "Bottled (tank)")],
        bindings=[CheckOption("Utility", "utility"), CheckOption("BottledTank", "bottled")],
    ),
]

# -- Chapter: outside and parking -----------------------------------------
QUESTIONS += inventory("outside", "Yard", [
    ("A.rain_gutters", "Rain Gutters", "RainGutters"),
    ("A.sprinklers", "Sprinklers", "Sprinklers"),
    ("A.patio_decking", "Patio/Decking", "PatioDecking"),
    ("A.builtin_bbq", "Built-in Barbecue", "BuiltinBarbecue"),
    ("A.gazebo", "Gazebo", "Gazebo"),
    ("A.sauna", "Sauna", "Sauna"),
])
QUESTIONS += [
    Question(
        id="A.garage",
        chapter="outside",
        group="Parking",
        prompt="Is there a garage?",
        kind="bool",
        lane=Lane.TAP,
        why=Why.GATE,
        legal="[ ] Garage: [ ] Attached [ ] Not Attached",
        bindings=[Check("Garage")],
    ),
    Question(
        id="A.garage_attached",
        chapter="outside",
        group="Parking",
        prompt="Is it attached to the house?",
        kind="single",
        lane=Lane.TAP,
        why=Why.COMPOUND,
        depends_on="A.garage is true",
        options=[Option("attached", "Attached"), Option("detached", "Not attached")],
        bindings=[SingleCheck({"attached": "Attached", "detached": "NoAttachedGarage"})],
    ),
    item("A.carport", "outside", "Parking", "Carport", "Carport"),
    Question(
        id="A.garage_opener",
        chapter="outside",
        group="Parking",
        prompt="Is there an automatic garage door opener?",
        kind="bool",
        lane=Lane.TAP,
        why=Why.GATE,
        legal="[ ] Automatic Garage Door Opener(s) [ ] Number Remote Controls ____",
        bindings=[Check("AutomaticGarageDoorOpeners")],
    ),
    Question(
        id="A.garage_remotes",
        chapter="outside",
        group="Parking",
        prompt="How many remote controls will the buyer receive?",
        kind="int",
        lane=Lane.TAP,
        why=Why.PRECISION,
        depends_on="A.garage_opener is true",
        explain="Count the ones you will actually hand over, including any clipped in a car.",
        bindings=[Check("RemoteControlsYes"), Text("NumberRemoteControlsDigit")],  # count>0 enforced by resolver
    ),
    item("A.roofs", "outside", "Structure", "Roof(s)", "Roofs"),
    item("A.gas_starter", "outside", "Structure", "Gas Starter (fireplace)", "GasStarter"),
    Question(
        id="A.exhaust_fan_rooms",
        chapter="outside",
        group="Which rooms",
        prompt="Which rooms have exhaust fans?",
        kind="text",
        lane=Lane.TAP,
        why=Why.PRECISION,
        legal="Exhaust Fan(s) in ____",
        example="Kitchen, both bathrooms",
        bindings=[Text("ExhaustFanRooms")],
    ),
    Question(
        id="A.wiring_220_rooms",
        chapter="outside",
        group="Which rooms",
        prompt="Which rooms have 220 volt wiring?",
        kind="text",
        lane=Lane.TAP,
        why=Why.PRECISION,
        legal="220 Volt Wiring in ____",
        explain="220 volt outlets are the big ones - electric dryers, ranges, EV chargers, some workshop tools.",
        example="Laundry room, garage",
        bindings=[Text("220VoltWiringRooms")],
    ),
    Question(
        id="A.fireplace_rooms",
        chapter="outside",
        group="Which rooms",
        prompt="Which rooms have fireplaces?",
        kind="text",
        lane=Lane.TAP,
        why=Why.PRECISION,
        legal="Fireplace(s) in ____",
        example="Living room, primary bedroom",
        bindings=[Text("FireplaceRooms")],
    ),
]

# -- Chapter: pool and spa -------------------------------------------------
QUESTIONS += [
    Question(
        id="A.hot_tub",
        chapter="pool",
        group="Spa",
        prompt="Is there a hot tub or spa?",
        kind="bool",
        lane=Lane.TAP,
        why=Why.GATE,
        bindings=[Check("HotTubSpa")],
    ),
    Question(
        id="A.locking_cover",
        chapter="pool",
        group="Spa",
        prompt="Does it have a locking safety cover?",
        kind="bool",
        lane=Lane.TAP,
        why=Why.ENUMERATION,
        depends_on="A.hot_tub is true",
        bindings=[Check("LockingSafetyCover")],
    ),
    Question(
        id="A.pool",
        chapter="pool",
        group="Pool",
        prompt="Is there a pool?",
        kind="bool",
        lane=Lane.TAP,
        why=Why.GATE,
        explain=(
            "The form has no box for the pool itself, only for its safety barrier and heater. "
            "We ask anyway, so we do not have to guess from the barrier answer."
        ),
        bindings=[],  # Intentionally unbound: gates the two questions below.
    ),
    Question(
        id="A.child_barrier",
        chapter="pool",
        group="Pool",
        prompt="Does the pool have a child-resistant barrier?",
        kind="bool",
        lane=Lane.TAP,
        why=Why.ENUMERATION,
        depends_on="A.pool is true",
        legal="Pool: [ ] Child Resistant Barrier",
        bindings=[Check("ChildResistantBarrier")],
    ),
    Question(
        id="A.pool_heater",
        chapter="pool",
        group="Pool",
        prompt="What does the pool or spa heater run on?",
        kind="multi",
        lane=Lane.TAP,
        why=Why.COMPOUND,
        depends_on="A.pool is true or A.hot_tub is true",
        legal="[ ] Pool/Spa Heater: [ ] Gas [ ] Solar [ ] Electric",
        options=[Option("gas", "Gas"), Option("solar", "Solar"), Option("electric", "Electric"), Option("none", "No heater")],
        bindings=[
            CheckAny("PoolSpaHeater", ["gas", "solar", "electric"]),
            CheckOption("Gas", "gas"),
            CheckOption("Solar", "solar", occurrence=0),
            CheckOption("Electric", "electric"),
        ],
    ),
]

# -- Chapter: anything not working (Section A catch-all) -------------------
QUESTIONS += [
    Question(
        id="A.not_working",
        chapter="working",
        prompt="Is anything on that list not working properly right now?",
        kind="tri",
        lane=Lane.TAP,
        why=Why.GATE,
        legal=(
            "Are there, to the best of your (Seller's) knowledge, any of the above that are "
            "not in operating condition? [ ] Yes [ ] No"
        ),
        explain=(
            "Only the things you just told us the house has. Not working means it does not do "
            "its job today - a dead burner, a garage remote that stopped pairing, an AC unit "
            "that runs but never gets cold."
        ),
        bindings=[TriCheck("KnowledgeYes", "KnowledgeNo")],
    ),
    Question(
        id="A.not_working_detail",
        chapter="working",
        prompt="Tell me what is not working.",
        kind="longtext",
        lane=Lane.VOICE,
        why=Why.NARRATIVE,
        depends_on="A.not_working is yes",
        legal="If yes, then describe. (Attach additional sheets if necessary.)",
        explain="Say them one at a time. It is fine to be rough about dates.",
        example="The second burner on the range does not light, and one garage remote stopped working last winter.",
        needs="For each item: which item, what it does wrong, roughly how long it has been like that.",
        bindings=[WrappedText(["YesNotWorkingDescription"], widths=[45])],
    ),
]

# -- Chapter: known defects (Section B) ------------------------------------
DEFECT_COMPONENTS = [
    ("interior_walls", "Interior Walls", "InteriorWalls"),
    ("ceilings", "Ceilings", "Ceilings"),
    ("floors", "Floors", "Floors"),
    ("exterior_walls", "Exterior Walls", "ExteriorWalls"),
    ("insulation", "Insulation", "Insulation"),
    ("roofs", "Roof(s)", "Roofs2"),
    ("windows", "Windows", "Windows"),
    ("doors", "Doors", "Doors"),
    ("foundation", "Foundation", "Foundation"),
    ("slabs", "Slab(s)", "Slabs"),
    ("driveways", "Driveways", "Driveways"),
    ("sidewalks", "Sidewalks", "Sidewalks"),
    ("walls_fences", "Walls/Fences", "WallsFences"),
    ("electrical", "Electrical Systems", "Electrical Systems"),
    ("plumbing", "Plumbing/Sewers/Septics", "PlumbingSewersSeptics"),
    ("other", "Other components", "Other2"),
]

QUESTIONS += [
    Question(
        id="B.gate",
        chapter="defects",
        prompt="Are you aware of any significant problems with the structure of the house?",
        kind="tri",
        lane=Lane.TAP,
        why=Why.GATE,
        legal=(
            "Are you (Seller) aware of any significant defects/malfunctions in any of the "
            "following? [ ] Yes [ ] No. If yes, check appropriate space(s) below."
        ),
        explain=(
            "Significant means it would matter to someone buying the house - not a scuff on a "
            "wall. Cracked slab, a roof that leaks, a window that will not close, wiring that "
            "trips. If you are unsure whether something counts, say so and we will look at it "
            "together rather than guess."
        ),
        bindings=[TriCheck("DefectsYes", "DefectsNo")],
    ),
    Question(
        id="B.components",
        chapter="defects",
        prompt="Which parts of the house?",
        kind="multi",
        lane=Lane.TAP,
        why=Why.ENUMERATION,
        depends_on="B.gate is yes",
        explain="Pick every one that has a problem. You will describe them next.",
        options=[Option(oid, label) for oid, label, _ in DEFECT_COMPONENTS],
        bindings=[CheckOption(name, oid) for oid, _, name in DEFECT_COMPONENTS],
    ),
    Question(
        id="B.other_describe",
        chapter="defects",
        prompt="What other component?",
        kind="text",
        lane=Lane.TAP,
        why=Why.PRECISION,
        depends_on="B.components contains other",
        legal="Other Components (Describe: ____)",
        bindings=[WrappedText(["Other2Describe", "Other2Describe#1"], widths=[15, 107])],
    ),
    Question(
        id="B.explain",
        chapter="defects",
        prompt="Tell me about each one.",
        kind="longtext",
        lane=Lane.VOICE,
        why=Why.NARRATIVE,
        depends_on="B.gate is yes",
        legal="If any of the above is checked, explain. (Attach additional sheets if necessary.)",
        explain=(
            "Go one part at a time. What is wrong, roughly when it started, and whether anyone "
            "has repaired or looked at it."
        ),
        example=(
            "The roof leaked over the back bedroom in the 2023 storms. A roofer replaced the "
            "flashing that spring and it has been dry since."
        ),
        needs="For each checked component: what is wrong, when it started, and whether it was repaired.",
        bindings=[WrappedText(["Describe1", "Describe2", "Describe3"], widths=[42, 107, 107])],
    ),
]

# -- Chapter: what you know (Section C) ------------------------------------
# All sixteen default to voice. These are the questions where sellers stall, not
# because they are unwilling but because the printed wording is unanswerable.
QUESTIONS += [
    aware(
        "C.hazards", 1,
        "Has anything ever turned up on the property that could be a health hazard?",
        "Asbestos, lead paint, mould, radon, a buried fuel or chemical tank, contaminated soil "
        "or water. Older homes often have some of this and disclosing it is normal.",
        "We had mould behind the laundry wall in 2022; it was removed and the wall rebuilt.",
        "AwareHazardsYes", "AwareHazardsNo",
        "Substances, materials, or products which may be an environmental hazard such as, but "
        "not limited to, asbestos, formaldehyde, radon gas, lead-based paint, mold, fuel or "
        "chemical storage tanks, and contaminated soil or water on the subject property.",
    ),
    aware(
        "C.shared", 2,
        "Do you share a fence, wall or driveway with a neighbour?",
        "Anything physical on the boundary where it is not clearly just yours - and especially "
        "where who pays to fix it has never been settled.",
        "The back fence is shared with the neighbour and we split the cost when it blew down.",
        "SharedYes", "SharedNo",
        "Features of the property shared in common with adjoining landowners, such as walls, "
        "fences, and driveways, whose use or responsibility for maintenance may have an effect "
        "on the subject property.",
    ),
    aware(
        "C.encroachments", 3,
        "Does anyone else have a right to use part of your land?",
        "An easement is a legal right for someone else to cross or use your property - a "
        "utility company's access strip, a shared path, a neighbour's driveway that clips your "
        "corner. An encroachment is a structure sitting over the property line.",
        "The power company has an access easement along the rear ten feet.",
        "AffectedInterestYes", "AffectedInterestNo",
        "Any encroachments, easements or similar matters that may affect your interest in the "
        "subject property.",
    ),
    aware(
        "C.no_permits", 4,
        "Has any work been done on the house without a permit?",
        "Additions, structural changes, or repairs where nobody pulled a permit. Very common "
        "with converted garages, patio covers and older bathroom work.",
        "The previous owner enclosed the patio; we have never found a permit for it.",
        "RoomAdditionsYes", "RoomAdditionsNo",
        "Room additions, structural modifications, or other alterations or repairs made without "
        "necessary permits.",
    ),
    aware(
        "C.not_to_code", 5,
        "Was any of that work not up to building code?",
        "Different from the permit question. Work can be permitted but still fail code, or be "
        "unpermitted and perfectly sound.",
        "The converted garage has no egress window, which an inspector flagged.",
        "RoomAdditionsCodeYes", "RoomAdditionsCodeNo",
        "Room additions, structural modifications, or other alterations or repairs not in "
        "compliance with building codes.",
    ),
    aware(
        "C.fill", 6,
        "Is any part of the lot built on fill?",
        "Fill is soil trucked in to level or extend the ground, rather than the original earth. "
        "It matters because fill settles. Hillside lots and pads cut into a slope often have it.",
        "The back third of the yard was filled to level it before we bought.",
        "FillYes", "FillNo",
        "Fill (compacted or otherwise) on the property or any portion thereof.",
    ),
    aware(
        "C.settling", 7,
        "Has the ground moved - settling, sliding, or soil problems?",
        "Signs are cracks that keep reopening after being patched, doors that stop closing, or "
        "a slope that has crept.",
        "There is a stair-step crack in the garage slab that we have patched twice.",
        "SettlingYes", "SettlingNo",
        "Any settling from any cause, or slippage, sliding, or other soil problems.",
    ),
    aware(
        "C.flooding", 8,
        "Any flooding, drainage or grading problems?",
        "Water going where it should not - a yard that ponds, a crawlspace that takes water, a "
        "slope that sends runoff at the house.",
        "The side yard ponds in heavy rain and drains within a day.",
        "FloodingYes", "FloodingNo",
        "Flooding, drainage or grading problems.",
    ),
    aware(
        "C.major_damage", 9,
        "Has the property ever had major damage from fire, earthquake, flood or landslide?",
        "Major means structural, or something that went through insurance. Include damage that "
        "was fully repaired - repaired damage still gets disclosed.",
        "Kitchen fire in 2019; insurance paid and the kitchen was rebuilt.",
        "DamageYes", "DamageNo",
        "Major damage to the property or any of the structures from fire, earthquake, floods, "
        "or landslides.",
    ),
    aware(
        "C.zoning", 10,
        "Any zoning violations or setback problems?",
        "A setback is the minimum distance a structure must sit from the property line. A shed "
        "or addition too close to the boundary is the usual case.",
        "The shed sits about two feet from the line and the city requires five.",
        "ZoningYes", "ZoningNo",
        "Any zoning violations, nonconforming uses, violations of 'setback' requirements.",
    ),
    aware(
        "C.noise", 11,
        "Are there noise problems or other nuisances in the neighbourhood?",
        "Persistent things a buyer would want to know before moving in - a flight path, a late "
        "venue, a neighbour dispute, a dog that never stops.",
        "The bar behind us has live music Friday and Saturday until about midnight.",
        "NoiseYes", "NoiseNo",
        "Neighborhood noise problems or other nuisances.",
    ),
    aware(
        "C.ccrs", 12,
        "Are there CC&Rs or other deed restrictions on the property?",
        "CC&Rs are rules recorded against the land itself that bind whoever owns it - paint "
        "colours, what can be parked outside, whether it can be rented short-term.",
        "The tract has CC&Rs limiting exterior paint colours and RV parking.",
        "CCRYes", "CCRNo",
        "CC&R's or other deed restrictions or obligations.",
    ),
    aware(
        "C.hoa", 13,
        "Is there a homeowners' association with authority over the property?",
        "If you pay dues to an association, or one can fine you or approve changes, the answer "
        "is yes.",
        "Yes, the association charges dues quarterly and approves exterior changes.",
        "HOAAuthorityYes", "HOAAuthorityNo",
        "Homeowners' Association which has any authority over the subject property.",
    ),
    aware(
        "C.common_area", 14,
        "Is there any common area you co-own with others?",
        "Shared facilities you own a slice of rather than rent - a pool, a private road, a "
        "greenbelt, tennis courts.",
        "We co-own the private road and the pool with eleven other houses.",
        "CommonYes", "CommonNo",
        "Any 'common area' (facilities such as pools, tennis courts, walkways, or other areas "
        "co-owned in undivided interest with others).",
    ),
    aware(
        "C.abatement", 15,
        "Has the property ever received a citation or notice of abatement?",
        "A written notice from a city or county ordering something be fixed or stopped.",
        "The city sent a notice about the dead tree in the parkway in 2021.",
        "AbatementYes", "AbatementNo",
        "Any notices of abatement or citations against the property.",
    ),
    aware(
        "C.lawsuits", 16,
        "Are there any lawsuits or claims involving this property?",
        "Includes construction defect claims and warranty claims, whether you brought them or "
        "someone brought them against you, and claims about shared common areas.",
        "The association is suing the original builder over the roofs.",
        "LawsuitsYes", "LawsuitsNo",
        "Any lawsuits by or against the Seller threatening to or affecting this real property, "
        "claims for damages by the Seller pursuant to Section 910 or 914, claims for breach of "
        "warranty pursuant to Section 900, or claims for breach of an enhanced protection "
        "agreement pursuant to Section 903, including any lawsuits or claims for damages "
        "pursuant to Section 910 or 914 alleging a defect or deficiency in this real property "
        "or 'common areas'.",
    ),
    Question(
        id="C.explain",
        chapter="awareness",
        prompt="Let's go through the ones you said yes to.",
        kind="longtext",
        lane=Lane.VOICE,
        why=Why.NARRATIVE,
        depends_on="any C.* is yes",
        legal="If the answer to any of these is yes, explain. (Attach additional sheets if necessary.)",
        explain=(
            "The form gives all sixteen questions one shared explanation box, so we keep them "
            "labelled as we go and assemble it for you."
        ),
        needs=(
            "One labelled passage per yes answer. Each needs what happened, roughly when, and "
            "the current status."
        ),
        bindings=[
            WrappedText(
                ["IfYesExplain1", "IfYesExplain2", "IfYesExplain3", "IfYesExplain4", "IfYesExplain5"],
                widths=[42, 107, 107, 107, 107],
            )
        ],
    ),
]

QUESTIONS_BY_ID: dict[str, Question] = {q.id: q for q in QUESTIONS}
SELLER_QUESTIONS = [q for q in QUESTIONS if CHAPTERS_BY_ID[q.chapter].audience == "seller"]
AGENT_QUESTIONS = [q for q in QUESTIONS if CHAPTERS_BY_ID[q.chapter].audience == "agent"]


def questions_for_chapter(chapter_id: str) -> list[Question]:
    return [q for q in QUESTIONS if q.chapter == chapter_id]
