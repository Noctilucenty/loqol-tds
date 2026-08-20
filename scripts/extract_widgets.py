#!/usr/bin/env python
"""Re-extract widget geometry from the source PDF into data/tds_widgets.json.

One widget = one placed box on a page. This is deliberately not one row per
AcroForm *field*, because several field names in this document own more than one
widget, and collapsing them is what makes the form impossible to fill correctly.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

from pypdf import PdfReader

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "data" / "CA-TDS.pdf"
OUT = ROOT / "data" / "tds_widgets.json"


def field_name(annot) -> str:
    """Full dotted name, walking up through /Parent."""
    chain = []
    node = annot
    while node is not None:
        title = node.get("/T")
        if title:
            chain.insert(0, str(title))
        parent = node.get("/Parent")
        node = parent.get_object() if parent is not None else None
    return ".".join(chain)


def field_type(annot) -> str:
    ft = annot.get("/FT")
    if ft is None and annot.get("/Parent") is not None:
        ft = annot["/Parent"].get_object().get("/FT")
    return str(ft)


def main() -> int:
    reader = PdfReader(str(SOURCE))
    rows = []
    for page_index, page in enumerate(reader.pages, start=1):
        for ref in page.get("/Annots") or []:
            annot = ref.get_object()
            if annot.get("/Subtype") != "/Widget":
                continue
            states = None
            ap = annot.get("/AP")
            if ap and "/N" in ap:
                try:
                    states = [str(k) for k in ap["/N"].get_object().keys()]
                except Exception:
                    states = None
            rows.append({
                "page": page_index,
                "name": field_name(annot),
                "ft": field_type(annot),
                "rect": [round(float(v), 1) for v in annot["/Rect"]],
                "states": states,
            })

    OUT.write_text(json.dumps(rows, indent=1))
    print(f"{len(rows)} widgets -> {OUT.relative_to(ROOT)}")

    from collections import Counter
    print("  by page:", dict(Counter(r["page"] for r in rows)))
    print("  by type:", dict(Counter(r["ft"] for r in rows)))
    dupes = {n: c for n, c in Counter(r["name"] for r in rows).items() if c > 1}
    if dupes:
        print("  names owning multiple widgets:", dupes)
    return 0


if __name__ == "__main__":
    sys.exit(main())
