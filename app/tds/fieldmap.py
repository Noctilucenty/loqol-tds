"""Ground truth about the PDF's widgets, and the answer -> field resolver.

`data/tds_widgets.json` is extracted from the shipped PDF by
`scripts/extract_widgets.py`. Each entry is one *widget* (a placed box on a
page), not one AcroForm field - the distinction matters because several field
names in this document own more than one widget.

Occurrence indices are assigned in reading order (page, then top-to-bottom, then
left-to-right) so that `Solar#0` is always the Pool/Spa Heater box and `Solar#1`
is always the Water Heater box, regardless of the order the PDF happens to store
its annotations in.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any, Iterable

from .bindings import FieldWrite
from .questions import QUESTIONS, QUESTIONS_BY_ID, Question

DATA = Path(__file__).resolve().parents[2] / "data"
WIDGETS_PATH = DATA / "tds_widgets.json"

PAGE_W, PAGE_H = 612.0, 792.0


@dataclass(frozen=True)
class Widget:
    name: str
    occurrence: int
    page: int
    ft: str
    x0: float
    y0: float
    x1: float
    y1: float

    @property
    def key(self) -> str:
        return self.name if self.occurrence == 0 else f"{self.name}#{self.occurrence}"

    @property
    def is_checkbox(self) -> bool:
        return self.ft == "/Btn"

    @property
    def is_signature(self) -> bool:
        return self.ft == "/Sig"

    def docuseal_area(self) -> dict[str, float | int]:
        """PDF points (origin bottom-left) -> DocuSeal fractions (origin top-left)."""
        return {
            "x": round(self.x0 / PAGE_W, 6),
            "y": round((PAGE_H - self.y1) / PAGE_H, 6),
            "w": round((self.x1 - self.x0) / PAGE_W, 6),
            "h": round((self.y1 - self.y0) / PAGE_H, 6),
            "page": self.page,
        }

    @property
    def char_capacity(self) -> int:
        """Roughly how many characters fit, at the ~9pt the form is set in."""
        return max(int((self.x1 - self.x0) / 5.0), 1)


@lru_cache(maxsize=1)
def widgets() -> list[Widget]:
    raw = json.loads(WIDGETS_PATH.read_text())
    # Reading order, so occurrence indices are stable and meaningful.
    raw.sort(key=lambda r: (r["page"], -r["rect"][3], r["rect"][0]))
    seen: dict[str, int] = {}
    out: list[Widget] = []
    for r in raw:
        name = r["name"]
        occ = seen.get(name, 0)
        seen[name] = occ + 1
        x0, y0, x1, y1 = r["rect"]
        out.append(Widget(name, occ, r["page"], r["ft"], x0, y0, x1, y1))
    return out


@lru_cache(maxsize=1)
def widgets_by_key() -> dict[str, Widget]:
    return {w.key: w for w in widgets()}


# --------------------------------------------------------------------------
# Validation
# --------------------------------------------------------------------------

def validate() -> list[str]:
    """Every binding must address a widget that exists. Returns problems."""
    problems: list[str] = []
    known = widgets_by_key()
    bound: set[str] = set()

    for q in QUESTIONS:
        for binding in q.bindings:
            for key in binding.field_keys():
                bound.add(key)
                if key not in known:
                    problems.append(f"{q.id}: binds unknown widget {key!r}")
                    continue
                w = known[key]
                writes = list(binding.writes(_probe_value(q)))
                for fw in writes:
                    if fw.key != key:
                        continue
                    if isinstance(fw.value, bool) and not w.is_checkbox:
                        problems.append(f"{q.id}: checkbox write to text widget {key!r}")
                    if isinstance(fw.value, str) and w.is_checkbox:
                        problems.append(f"{q.id}: text write to checkbox widget {key!r}")

    # Signatures and per-page initials are handled by DocuSeal roles, not answers.
    role_owned = {k for k, w in known.items() if w.is_signature or _is_role_field(k)}
    unbound = {k for k in known if k not in bound and k not in role_owned}
    for key in sorted(unbound):
        problems.append(f"unbound widget {key!r} (page {known[key].page})")
    return problems


def _is_role_field(name: str) -> bool:
    """Owned by a signer role, or filled from deal metadata - either way, not an answer."""
    from .roles import ROLE_WIDGET_KEYS, SYSTEM_FIELDS
    return name in ROLE_WIDGET_KEYS or name in SYSTEM_FIELDS


def _probe_value(q: Question) -> Any:
    """A representative answer, used only to type-check bindings."""
    match q.kind:
        case "bool":
            return True
        case "tri":
            return "yes"
        case "multi":
            return [o.id for o in q.options]
        case "single":
            return q.options[0].id if q.options else ""
        case "int":
            return 3
        case _:
            return "probe"


# --------------------------------------------------------------------------
# Resolution
# --------------------------------------------------------------------------

def resolve(answers: dict[str, Any]) -> tuple[dict[str, Any], list[str]]:
    """Turn seller answers into `{widget_key: value}` plus any overflow text.

    Only questions that are actually *shown* contribute. A pool heater answer
    left behind by a seller who then said they have no pool must not print.
    """
    from .gating import is_visible  # local import: gating reads the graph

    out: dict[str, Any] = {}
    overflow: list[str] = []

    for q in QUESTIONS:
        if q.id not in answers:
            continue
        if not is_visible(q, answers):
            continue
        value = answers[q.id]
        if value is None:
            continue
        for binding in q.bindings:
            layout = getattr(binding, "layout", None)
            if layout is not None:
                _, spilled = layout(value)
                if spilled:
                    # The addendum restates the answer in full. A continuation
                    # sheet that opens mid-sentence is not a usable disclosure -
                    # the reader must be able to take the item whole from here.
                    overflow.append(f"{_addendum_heading(q)}\n{str(value).strip()}")
            for fw in binding.writes(value):
                out[fw.key] = fw.value
    return out, overflow


SECTION_NAMES = {
    "I.": "Section I - Coordination with other disclosure documents",
    "P.": "Section II - Seller's information",
    "A.": "Section A - Items and operating condition",
    "B.": "Section B - Significant defects or malfunctions",
    "C.": "Section C - Seller awareness",
}


def _addendum_heading(q: Question) -> str:
    section = next((v for k, v in SECTION_NAMES.items() if q.id.startswith(k)), "Additional disclosure")
    detail = (q.legal or q.prompt).strip().rstrip(".")
    if len(detail) > 150:
        detail = detail[:147].rstrip() + "..."
    return f"{section}: {detail}"


def coverage() -> dict[str, Any]:
    """How much of the form the question graph actually reaches."""
    known = widgets_by_key()
    bound = {k for q in QUESTIONS for b in q.bindings for k in b.field_keys()}
    role = {k for k, w in known.items() if w.is_signature or _is_role_field(k)}
    return {
        "widgets_total": len(known),
        "bound_by_questions": len(bound & set(known)),
        "handled_by_signer_roles": len(role),
        "unhandled": sorted(set(known) - bound - role),
    }
