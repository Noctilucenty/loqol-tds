#!/usr/bin/env python
"""Build the DocuSeal template from measured widget geometry.

Run once per DocuSeal account, then put the printed id in DOCUSEAL_TEMPLATE_ID.

The template is declared from coordinates rather than inherited from the PDF's
AcroForm, which is what lets the two colliding field names become two
independently addressable boxes, and what lets the three agent signature lines -
printed on the form with no fields behind them - exist at all.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.config import settings
from app.docuseal import DocuSealClient, DocuSealError, build_fields
from app.tds.fieldmap import validate


def main() -> int:
    problems = validate()
    if problems:
        print("field bindings are invalid; refusing to build a template:")
        for p in problems:
            print("  -", p)
        return 1

    if not settings().docuseal_enabled:
        print("DOCUSEAL_API_KEY is not set.")
        return 1

    fields = build_fields()
    from collections import Counter
    print(f"{len(fields)} fields")
    print("  by type:", dict(Counter(f["type"] for f in fields)))
    print("  by role:", dict(Counter(f["role"] for f in fields)))

    try:
        template = DocuSealClient().create_template()
    except DocuSealError as exc:
        print("failed:", exc)
        return 1

    print()
    print(f"  DOCUSEAL_TEMPLATE_ID={template['id']}")
    print(f"  https://docuseal.com/templates/{template['id']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
