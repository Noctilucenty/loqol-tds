"""Render answers onto the TDS locally.

This exists for two reasons. It is the preview the agent reviews before anything
is sent for signature, and it is the fallback that keeps the app working end to
end when no DocuSeal key is configured - a reviewer who clones the repo with no
credentials still gets a filled, correct PDF.

It does not fill the AcroForm. It stamps.

Filling by field value is impossible on this document: `Solar` is one field
owning two widgets on two different lines, and `Other2Describe` owns two widgets
that need different text. An AcroForm value is per-field, so any value-based fill
either checks both Solar boxes or neither. Treating the form as a coordinate
source and drawing onto the page sidesteps that completely, and has the useful
side effect of producing genuinely flat output - the same thing DocuSeal does
when it burns a submission in.
"""

from __future__ import annotations

import io
from datetime import date
from pathlib import Path
from typing import Any

from pypdf import PdfReader, PdfWriter
from reportlab.lib.colors import Color, black
from reportlab.pdfgen import canvas

from .fieldmap import DATA, PAGE_H, PAGE_W, widgets_by_key
from .roles import ROLE_FIELDS, SYSTEM_FIELDS

SOURCE_PDF = DATA / "CA-TDS.pdf"
INK = Color(0.05, 0.11, 0.24)  # near-black navy, reads as pen on a scan


def _fit_font_size(height: float, cap: float = 9.0) -> float:
    return max(min(cap, height - 2.0), 5.5)


def _draw_check(c: canvas.Canvas, x0: float, y0: float, x1: float, y1: float) -> None:
    """A hand-ish check mark inscribed in the widget box."""
    w, h = x1 - x0, y1 - y0
    c.saveState()
    c.setStrokeColor(INK)
    c.setLineWidth(max(min(w, h) * 0.17, 0.7))
    c.setLineCap(1)
    p = c.beginPath()
    p.moveTo(x0 + w * 0.18, y0 + h * 0.52)
    p.lineTo(x0 + w * 0.42, y0 + h * 0.22)
    p.lineTo(x0 + w * 0.86, y0 + h * 0.80)
    c.drawPath(p)
    c.restoreState()


def _draw_text(c: canvas.Canvas, x0: float, y0: float, x1: float, y1: float, text: str) -> None:
    if not text:
        return
    size = _fit_font_size(y1 - y0)
    c.setFont("Helvetica", size)
    c.setFillColor(INK)
    max_w = (x1 - x0) - 2.0
    # Shrink to fit rather than overflow the ruled line.
    while size > 5.0 and c.stringWidth(text, "Helvetica", size) > max_w:
        size -= 0.25
        c.setFont("Helvetica", size)
    c.drawString(x0 + 1.5, y0 + (y1 - y0 - size) / 2 + 1.2, text)


def render(
    field_values: dict[str, Any],
    *,
    system: dict[str, str] | None = None,
    signatures: dict[str, str] | None = None,
    overflow: list[str] | None = None,
) -> bytes:
    """Stamp `field_values` (keyed by widget key) onto the form.

    `system` supplies the header fields (address, disclosure date). `signatures`
    supplies typed names/initials for role fields, used only for the agent's
    preview - the legally binding signature is captured by DocuSeal.
    """
    known = widgets_by_key()
    system = system or {}
    signatures = signatures or {}

    reader = PdfReader(str(SOURCE_PDF))
    overlays: list[bytes] = []

    for page_no in range(1, len(reader.pages) + 1):
        buf = io.BytesIO()
        c = canvas.Canvas(buf, pagesize=(PAGE_W, PAGE_H))

        for key, widget in known.items():
            if widget.page != page_no:
                continue

            value: Any = None
            if key in field_values:
                value = field_values[key]
            elif key in SYSTEM_FIELDS:
                value = system.get(SYSTEM_FIELDS[key], "")

            if value is None or value == "" or value is False:
                continue
            if widget.is_checkbox:
                if value:
                    _draw_check(c, widget.x0, widget.y0, widget.x1, widget.y1)
            else:
                _draw_text(c, widget.x0, widget.y0, widget.x1, widget.y1, str(value))

        # Role fields the PDF has no widgets for (the three agent lines).
        for rf in ROLE_FIELDS:
            if not rf.rect or rf.name not in signatures:
                continue
            pg, x0, y0, x1, y1 = rf.rect
            if pg != page_no:
                continue
            _draw_text(c, x0, y0, x1, y1, str(signatures[rf.name]))

        c.save()
        overlays.append(buf.getvalue())

    # Clone into the writer *before* merging. pypdf only guarantees a correct
    # merge for pages already owned by a writer; stamping a detached reader page
    # happens to work and is documented as unreliable.
    writer = PdfWriter(clone_from=reader)
    for i, page in enumerate(writer.pages):
        overlay = PdfReader(io.BytesIO(overlays[i])).pages[0]
        page.merge_page(overlay)
        # Drop the interactive layer: the output is a record, not a form.
        page.pop("/Annots", None)

    if overflow:
        _append_addendum(writer, overflow, system)

    # Remove the document-level AcroForm so no viewer offers to "fill" a
    # document that has already been answered.
    if "/AcroForm" in writer._root_object:
        del writer._root_object["/AcroForm"]

    out = io.BytesIO()
    writer.write(out)
    return out.getvalue()


def _append_addendum(writer: PdfWriter, overflow: list[str], system: dict[str, str]) -> None:
    """The form's own 'attach additional sheets if necessary' escape hatch.

    Anything that did not fit on a ruled line lands here in full, rather than
    being truncated. A truncated disclosure is a defective disclosure.
    """
    buf = io.BytesIO()
    c = canvas.Canvas(buf, pagesize=(PAGE_W, PAGE_H))
    y = PAGE_H - 64

    c.setFont("Helvetica-Bold", 12)
    c.setFillColor(black)
    c.drawString(54, y, "ADDENDUM TO REAL ESTATE TRANSFER DISCLOSURE STATEMENT")
    y -= 18
    c.setFont("Helvetica", 9)
    c.drawString(54, y, f"Property: {system.get('property_address', '')}")
    y -= 12
    c.drawString(54, y, f"Date: {system.get('disclosure_date', date.today().isoformat())}")
    y -= 24

    for block in overflow:
        head, _, body = block.partition("\n")
        c.setFont("Helvetica-Bold", 9.5)
        for line in _wrap(head, 92):
            c.drawString(54, y, line)
            y -= 12
        c.setFont("Helvetica", 9.5)
        for line in _wrap(body, 100):
            if y < 60:
                c.showPage()
                y = PAGE_H - 64
                c.setFont("Helvetica", 9.5)
            c.drawString(54, y, line)
            y -= 12
        y -= 10

    c.save()
    for page in PdfReader(io.BytesIO(buf.getvalue())).pages:
        writer.add_page(page)


def _wrap(text: str, width: int) -> list[str]:
    import textwrap

    return textwrap.wrap(text, width=width) or [""]
