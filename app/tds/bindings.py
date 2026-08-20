"""How one seller answer becomes one or more fields on the TDS.

A binding is deliberately a small, dumb object. All the judgement lives in the
question graph; this layer only knows how to turn a Python value into the
strings and check-states the PDF (and DocuSeal) expect.

Three facts about the source PDF drive the design here:

1.  It is not flat. It ships with a complete AcroForm - 159 placed widgets. The
    field names in it are decent, so they are used as the canonical binding keys.

2.  Some field names are bound to more than one widget. `Solar` is a single
    AcroForm field with two kid widgets: one on the Pool/Spa Heater line and one
    on the Water Heater line. Setting it checks both, which makes the form
    impossible to fill correctly through the AcroForm alone. Bindings therefore
    address a *widget*, identified by name plus an occurrence index, and the
    DocuSeal template splits the collision into two independently named fields.

3.  Several free-text answers have to be written across a run of single-line
    fields (`IfYesExplain1..5`, `Describe1..3`). `WrappedText` owns that, so the
    question graph can pretend it is writing one paragraph.
"""

from __future__ import annotations

import textwrap
from dataclasses import dataclass, field
from typing import Any, Iterable


#: The sentinel `answers_dict` uses for an explicit "I don't know". Only the
#: paired Yes/No bindings can render it; every checkbox must read it as "no tick",
#: because `bool("unknown")` is True and would print an affirmative answer.
UNKNOWN = "unknown"


def _is_on(value: Any) -> bool:
    if value is None or value is False:
        return False
    if isinstance(value, str):
        text = value.strip().lower()
        if text == UNKNOWN:
            return False
        # A count that arrived as text still means what it says. "0 remote
        # controls" must not tick "Remote Controls: yes" just because bool("0")
        # is True.
        if text.lstrip("-").isdigit():
            return int(text) != 0
    return bool(value)


@dataclass(frozen=True)
class FieldWrite:
    """A resolved instruction to put `value` into one widget."""

    name: str
    value: str | bool
    occurrence: int = 0

    @property
    def key(self) -> str:
        """Canonical key. Disambiguates duplicate AcroForm names."""
        return self.name if self.occurrence == 0 else f"{self.name}#{self.occurrence}"


class Binding:
    """Base class. Subclasses turn an answer value into FieldWrites."""

    def writes(self, value: Any) -> Iterable[FieldWrite]:  # pragma: no cover
        raise NotImplementedError

    def field_keys(self) -> Iterable[str]:  # pragma: no cover
        raise NotImplementedError


@dataclass
class Check(Binding):
    """A checkbox that is on when the answer is truthy."""

    name: str
    occurrence: int = 0
    invert: bool = False

    def writes(self, value: Any) -> Iterable[FieldWrite]:
        on = _is_on(value)
        if self.invert:
            on = not on
        yield FieldWrite(self.name, on, self.occurrence)

    def field_keys(self) -> Iterable[str]:
        yield FieldWrite(self.name, False, self.occurrence).key


@dataclass
class CheckOption(Binding):
    """A checkbox that is on when `option` is among the selected options.

    Used for multi-selects such as `Water Heater: Gas / Solar / Electric`, which
    the form presents as one question and three boxes.
    """

    name: str
    option: str
    occurrence: int = 0

    def writes(self, value: Any) -> Iterable[FieldWrite]:
        selected = value or []
        if isinstance(selected, str):
            selected = [] if selected.strip().lower() == UNKNOWN else [selected]
        yield FieldWrite(self.name, self.option in selected, self.occurrence)

    def field_keys(self) -> Iterable[str]:
        yield FieldWrite(self.name, False, self.occurrence).key


@dataclass
class TriCheck(Binding):
    """The form's ubiquitous paired Yes/No boxes.

    Crucially, `unknown` leaves *both* boxes clear. On a TDS that is the honest
    rendering of "I don't know" - checking No because the seller was unsure is
    how sellers end up in court, so the model refuses to collapse the two.
    """

    yes: str
    no: str
    yes_occurrence: int = 0
    no_occurrence: int = 0

    def writes(self, value: Any) -> Iterable[FieldWrite]:
        yield FieldWrite(self.yes, value == "yes", self.yes_occurrence)
        yield FieldWrite(self.no, value == "no", self.no_occurrence)

    def field_keys(self) -> Iterable[str]:
        yield FieldWrite(self.yes, False, self.yes_occurrence).key
        yield FieldWrite(self.no, False, self.no_occurrence).key


@dataclass
class SingleCheck(Binding):
    """One-of-N rendered as N checkboxes, e.g. Garage Attached / Not Attached."""

    mapping: dict[str, str] = field(default_factory=dict)
    occurrences: dict[str, int] = field(default_factory=dict)

    def writes(self, value: Any) -> Iterable[FieldWrite]:
        unknown = isinstance(value, str) and value.strip().lower() == UNKNOWN
        for option, name in self.mapping.items():
            yield FieldWrite(name, (not unknown) and value == option, self.occurrences.get(option, 0))

    def field_keys(self) -> Iterable[str]:
        for option, name in self.mapping.items():
            yield FieldWrite(name, False, self.occurrences.get(option, 0)).key


@dataclass
class Text(Binding):
    """A plain text field."""

    name: str
    occurrence: int = 0

    def writes(self, value: Any) -> Iterable[FieldWrite]:
        if value is None or (isinstance(value, str) and value.strip().lower() == UNKNOWN):
            yield FieldWrite(self.name, "", self.occurrence)
            return
        yield FieldWrite(self.name, str(value), self.occurrence)

    def field_keys(self) -> Iterable[str]:
        yield FieldWrite(self.name, "", self.occurrence).key


def wrap_variable(text: str, widths: list[int]) -> tuple[list[str], list[str]]:
    """Greedily wrap `text` into lines of differing maximum widths.

    The form's explanation areas are not a uniform block: Section B's "Other
    Components (Describe: ___)" is a 15-character stub that continues onto a
    full-width line, and Section C's shared explanation opens with a short line
    beside printed text before widening. A fixed wrap width would either overflow
    the stub or waste most of the wide lines.

    Returns (lines, leftover_words).
    """
    words = text.split()
    lines: list[str] = []
    for width in widths:
        if not words:
            lines.append("")
            continue
        line = ""
        while words:
            candidate = f"{line} {words[0]}".strip()
            if len(candidate) > width and line:
                break
            # A single word longer than the line gets hard-split rather than lost.
            if len(candidate) > width and not line:
                head, tail = words[0][:width], words[0][width:]
                words[0] = tail
                line = head
                break
            line = candidate
            words.pop(0)
        lines.append(line)
    return lines, words


@dataclass
class WrappedText(Binding):
    """One paragraph laid out across a run of single-line fields.

    The form gives Section C's sixteen questions a single shared explanation area
    made of five ruled lines of differing widths. Sellers do not think in ruled
    lines, so the app collects one narrative and this binding does the layout.

    Overflow is never silently truncated. If the text does not fit, the last
    visible line ends with a continuation marker and the remainder is returned as
    overflow, to be carried on an addendum page - the form's own "attach
    additional sheets if necessary" escape hatch.
    """

    names: list[str]
    widths: list[int] = field(default_factory=list)
    continuation: str = " (cont'd on attached sheet)"

    def _widths(self) -> list[int]:
        return self.widths or [96] * len(self.names)

    def layout(self, value: Any) -> tuple[list[str], str | None]:
        """Return (lines, overflow_text). No seller text is ever dropped."""
        text = (value or "").strip()
        widths = self._widths()
        if not text:
            return [""] * len(self.names), None

        lines, leftover = wrap_variable(text, widths)
        if not leftover:
            return lines, None

        # Give up room on the final visible line for the continuation marker.
        budget = widths[-1] - len(self.continuation)
        if budget >= 12:
            refit, spilled = wrap_variable(lines[-1], [budget])
            lines[-1] = refit[0] + self.continuation
            leftover = spilled + leftover
        return lines, " ".join(leftover).strip() or None

    def writes(self, value: Any) -> Iterable[FieldWrite]:
        lines, _ = self.layout(value)
        for name, line in zip(self.names, lines):
            key, _, occ = name.partition("#")
            yield FieldWrite(key, line, int(occ or 0))

    def field_keys(self) -> Iterable[str]:
        yield from self.names


@dataclass
class CheckAny(Binding):
    """A checkbox that is on when any of `options` is selected.

    The form's compound rows have a parent box and fuel boxes: `[ ] Pool/Spa
    Heater: [ ] Gas [ ] Solar [ ] Electric`. The parent must be on only when a
    real fuel was chosen, not merely because the question was answered - so it
    cannot be a plain truthiness check over the option list.
    """

    name: str
    options: list[str] = field(default_factory=list)
    occurrence: int = 0

    def writes(self, value: Any) -> Iterable[FieldWrite]:
        selected = value or []
        if isinstance(selected, str):
            selected = [] if selected.strip().lower() == UNKNOWN else [selected]
        yield FieldWrite(self.name, any(o in selected for o in self.options), self.occurrence)

    def field_keys(self) -> Iterable[str]:
        yield FieldWrite(self.name, False, self.occurrence).key
