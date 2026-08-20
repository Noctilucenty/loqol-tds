"""DocuSeal template construction and submission.

The brief says the PDF is flat and that building the template is part of the
exercise. The PDF is not flat - it carries a full AcroForm - but the template is
built from coordinates anyway, and that turns out to be the load-bearing
decision rather than a stylistic one.

Uploading the PDF and letting DocuSeal inherit its form fields would reproduce
the document's two defects: `Solar` is one field with widgets on both the
Pool/Spa Heater and Water Heater lines, so it can only ever be checked in both
places or neither, and `Other2Describe` owns two widgets that need different
text. Declaring fields from measured geometry gives every box its own name and
its own value, which is the only way this form can be filled correctly.

It also lets the three agent signature lines exist at all. The PDF prints them
with no fields behind them; here they are declared from the rules measured out of
the page's vector content.

Every field is assigned to a signer role. Answer fields belong to the Seller and
are pushed in read-only: by the time a submission is created the seller has
already answered them here, and the DocuSeal step is a signing ceremony, not a
second chance to retype a legal disclosure.
"""

from __future__ import annotations

import base64
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import httpx

from .config import settings
from .tds.fieldmap import DATA, widgets_by_key
from .tds.roles import ROLE_FIELDS, ROLE_LABELS, SYSTEM_FIELDS
from .tds.fieldmap import PAGE_H, PAGE_W

SOURCE_PDF = DATA / "CA-TDS.pdf"
TEMPLATE_NAME = "California TDS (Loqol)"


def safe_name(widget_key: str) -> str:
    """A DocuSeal-safe field name that still round-trips to the widget key."""
    return re.sub(r"[^A-Za-z0-9_]", "_", widget_key.replace("#", "__"))


def widget_key_for(field_name: str) -> str | None:
    for key in widgets_by_key():
        if safe_name(key) == field_name:
            return key
    return None


def _area_from_rect(page: int, x0: float, y0: float, x1: float, y1: float) -> dict[str, Any]:
    return {
        "x": round(x0 / PAGE_W, 6),
        "y": round((PAGE_H - y1) / PAGE_H, 6),
        "w": round((x1 - x0) / PAGE_W, 6),
        "h": round((y1 - y0) / PAGE_H, 6),
        "page": page,
    }


def build_fields() -> list[dict[str, Any]]:
    """Every field on the template, with its role and geometry."""
    known = widgets_by_key()
    role_widget_keys = {f.widget for f in ROLE_FIELDS if f.widget}
    fields: list[dict[str, Any]] = []

    # 1. Answer and system fields: Seller's, but read-only and prefilled.
    for key, w in known.items():
        if key in role_widget_keys or w.is_signature:
            continue
        fields.append({
            "name": safe_name(key),
            "type": "checkbox" if w.is_checkbox else "text",
            "role": ROLE_LABELS["seller"],
            "required": False,
            "readonly": True,
            "areas": [w.docuseal_area()],
            "preferences": {"font_size": 8, "align": "left", "valign": "center"},
        })

    # 2. Signer fields, including the three agent lines the PDF omits.
    for rf in ROLE_FIELDS:
        if rf.widget:
            w = known.get(rf.widget)
            if w is None:
                continue
            area = w.docuseal_area()
        elif rf.rect:
            area = _area_from_rect(*rf.rect)
        else:
            continue
        fields.append({
            "name": rf.name,
            "type": rf.type,
            "role": ROLE_LABELS[rf.role],
            "required": rf.required,
            "areas": [area],
        })

    return fields


def build_template_payload(pdf_bytes: bytes | None = None) -> dict[str, Any]:
    raw = pdf_bytes if pdf_bytes is not None else SOURCE_PDF.read_bytes()
    return {
        "name": TEMPLATE_NAME,
        "external_id": "loqol-ca-tds-v1",
        "documents": [{
            "name": "California TDS",
            "file": base64.b64encode(raw).decode(),
            "fields": build_fields(),
        }],
        # The source AcroForm must go. Leaving it would put a second, broken set
        # of inputs behind the fields declared above.
        "flatten": True,
    }


@dataclass
class SubmissionResult:
    submission_id: int
    submitters: list[dict[str, Any]]

    @property
    def seller_url(self) -> str | None:
        for s in self.submitters:
            if (s.get("role") or "").lower().startswith("seller") and s.get("embed_src"):
                return s["embed_src"]
        for s in self.submitters:
            if s.get("embed_src"):
                return s["embed_src"]
        return None


class DocuSealError(RuntimeError):
    pass


class DocuSealClient:
    def __init__(self, api_key: str | None = None, base_url: str | None = None) -> None:
        cfg = settings()
        self.api_key = api_key or cfg.docuseal_api_key
        self.base_url = (base_url or cfg.docuseal_base_url).rstrip("/")
        if not self.api_key:
            raise DocuSealError("DOCUSEAL_API_KEY is not configured")

    def _client(self) -> httpx.Client:
        return httpx.Client(
            base_url=self.base_url,
            headers={"X-Auth-Token": self.api_key, "Content-Type": "application/json"},
            timeout=90.0,
        )

    def _request(self, method: str, path: str, **kw: Any) -> Any:
        with self._client() as c:
            r = c.request(method, path, **kw)
        if r.status_code >= 400:
            raise DocuSealError(f"DocuSeal {method} {path} -> {r.status_code}: {r.text[:500]}")
        return r.json() if r.content else None

    def create_template(self, pdf_bytes: bytes | None = None) -> dict[str, Any]:
        return self._request("POST", "/templates/pdf", json=build_template_payload(pdf_bytes))

    def get_template(self, template_id: int) -> dict[str, Any]:
        return self._request("GET", f"/templates/{template_id}")

    def list_templates(self) -> dict[str, Any]:
        return self._request("GET", "/templates")

    def create_submission(
        self,
        template_id: int,
        *,
        seller_name: str,
        seller_email: str,
        values: dict[str, Any],
        co_seller: tuple[str, str] | None = None,
        send_email: bool = False,
        completed_redirect_url: str | None = None,
    ) -> SubmissionResult:
        """Create a signing request with every answer already filled in."""
        submitters: list[dict[str, Any]] = [{
            "role": ROLE_LABELS["seller"],
            "name": seller_name,
            "email": seller_email,
            "values": values,
            # Answers are locked. The seller reviewed them in our UI; DocuSeal is
            # where they sign, not where they re-enter a legal disclosure.
            "fields": [{"name": k, "readonly": True} for k in values],
            "send_email": send_email,
        }]
        if co_seller and co_seller[1]:
            submitters.append({
                "role": ROLE_LABELS["co_seller"],
                "name": co_seller[0],
                "email": co_seller[1],
                "send_email": send_email,
            })

        payload: dict[str, Any] = {
            "template_id": template_id,
            "send_email": send_email,
            "submitters": submitters,
        }
        if completed_redirect_url:
            payload["completed_redirect_url"] = completed_redirect_url

        data = self._request("POST", "/submissions", json=payload)
        # The API returns either a submission object or a list of submitters.
        if isinstance(data, list):
            sub_id = data[0].get("submission_id") or data[0].get("id")
            return SubmissionResult(submission_id=int(sub_id), submitters=data)
        return SubmissionResult(
            submission_id=int(data["id"]),
            submitters=data.get("submitters", []),
        )

    def get_submission(self, submission_id: int) -> dict[str, Any]:
        return self._request("GET", f"/submissions/{submission_id}")

    def get_documents(self, submission_id: int) -> dict[str, Any]:
        return self._request("GET", f"/submissions/{submission_id}/documents")


def values_for_submission(field_values: dict[str, Any], system: dict[str, str]) -> dict[str, Any]:
    """Widget-keyed answers -> DocuSeal field names."""
    out: dict[str, Any] = {}
    for key, value in field_values.items():
        out[safe_name(key)] = value
    for key, source in SYSTEM_FIELDS.items():
        if source in system:
            out[safe_name(key)] = system[source]
    return out
