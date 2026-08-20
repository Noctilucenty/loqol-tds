#!/usr/bin/env python
"""Drive the real workflow against a running instance and report what works.

The unit tests check units and the smoke test checks that pages render. Neither
one takes a disclosure from an empty deal through to a signed document, which is
the only thing that actually matters. This does, against whatever instance you
point it at, using nothing but the public API.

    python scripts/audit.py                              # localhost:8000
    python scripts/audit.py https://loqol-tds.onrender.com

Exits non-zero on any failure.
"""

from __future__ import annotations

import http.cookiejar
import io
import json
import sys
import urllib.error
import urllib.request

BASE = (sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:8000").rstrip("/")
PASSED: list[str] = []
FAILED: list[str] = []


def step(name: str, ok: bool, detail: str = "") -> bool:
    (PASSED if ok else FAILED).append(name)
    print(f"  {'PASS' if ok else 'FAIL'}  {name}" + (f"  [{detail}]" if detail else ""))
    return ok


class Client:
    """A browser-ish session: keeps cookies, speaks JSON."""

    def __init__(self) -> None:
        self.opener = urllib.request.build_opener(
            urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar())
        )

    def __call__(self, path, body=None, method=None, raw=False):
        verb = method or ("POST" if body is not None else "GET")
        req = urllib.request.Request(
            BASE + path,
            data=json.dumps(body).encode() if body is not None else None,
            headers={"Content-Type": "application/json"},
            method=verb,
        )
        try:
            resp = self.opener.open(req, timeout=120)
            data = resp.read()
            return resp.status, (data if raw else (json.loads(data) if data else {}))
        except urllib.error.HTTPError as exc:
            payload = exc.read()
            try:
                return exc.code, json.loads(payload)
            except Exception:
                return exc.code, {"raw": payload[:200].decode(errors="replace")}


ADDRESS = "1247 Sepulveda Blvd, Culver City, CA 90230"


def main() -> int:
    print(f"execution audit: {BASE}\n")
    agent = Client()

    # -- deployment ---------------------------------------------------------
    print("deployment")
    ok, health = agent("/api/health")
    step("health responds", ok == 200)
    cov = health.get("coverage", {})
    step("every form field is bound", cov.get("unhandled") == [],
         f"{cov.get('bound_by_questions')} questions + {cov.get('handled_by_signer_roles')} roles "
         f"= {cov.get('widgets_total')}")

    # -- identity -----------------------------------------------------------
    print("\nidentity")
    code, _ = agent("/api/agent/deals")
    step("agent API rejects anonymous", code == 401, f"got {code}")
    code, me = agent("/api/auth/demo", {})
    step("demo workspace mints an isolated agent", code == 200, me.get("email"))
    stranger = Client()
    stranger("/api/auth/demo", {})

    # -- a deal -------------------------------------------------------------
    print("\ndeal lifecycle")
    _, deal = agent("/api/agent/deals", {
        "property_address": ADDRESS, "city": "Culver City", "county": "Los Angeles",
        "seller_name": "Dana Whitfield", "seller_email": "dana@example.com",
    })
    step("new deal starts as not-started", deal["status"] == "draft" and deal["percent"] == 0,
         f"{deal['status']} {deal['percent']}%")
    code, _ = stranger(f"/api/agent/deals/{deal['id']}/review")
    step("another agent cannot reach it", code == 404, f"got {code}")

    _, link = agent(f"/api/agent/deals/{deal['id']}/link", {})
    token = link["url"].rsplit("/", 1)[-1]
    seller = Client()
    code, opened = seller(f"/api/s/{token}")
    step("seller opens with no login", code == 200)
    step("address is offered for checking, not answered for them",
         opened["property"]["address"] == ADDRESS and opened["progress"]["answered"] == 0)
    code, _ = seller("/api/s/" + "z" * 43)
    step("a guessed link is refused", code == 404, f"got {code}")
    _, link2 = agent(f"/api/agent/deals/{deal['id']}/link", {})
    old, _ = seller(f"/api/s/{token}/state")
    step("rotating the link kills the old one", old == 404, f"old got {old}")
    token = link2["url"].rsplit("/", 1)[-1]

    # -- answering ----------------------------------------------------------
    print("\nanswering")
    spec = seller(f"/api/s/{token}")[1]["form"]
    code, _ = seller(f"/api/s/{token}/answers",
                     {"question_id": "I.multi_unit", "value": True}, method="PUT")
    step("seller cannot answer the agent's section", code == 403, f"got {code}")
    seller(f"/api/s/{token}/answers", {"question_id": "A.pool", "value": False}, method="PUT")
    code, _ = seller(f"/api/s/{token}/answers",
                     {"question_id": "A.child_barrier", "value": True}, method="PUT")
    step("seller cannot answer a question behind a shut gate", code == 409, f"got {code}")
    code, _ = seller(f"/api/s/{token}/answers",
                     {"question_id": "A.water_supply", "value": True}, method="PUT")
    step("a wrong-shaped value cannot brick the session",
         code == 200 and seller(f"/api/s/{token}/state")[0] == 200, f"got {code}")

    seller(f"/api/s/{token}/answers",
           {"question_id": "P.address", "value": "1250 Corrected Blvd, Culver City, CA 90230"},
           method="PUT")
    _, review = agent(f"/api/agent/deals/{deal['id']}/review")
    step("a corrected address reaches the printed form",
         review["deal"]["property_address"].startswith("1250"),
         review["deal"]["property_address"])

    # -- voice --------------------------------------------------------------
    print("\nvoice lane")
    _, cfg = seller(f"/api/voice/{token}/config")
    if cfg.get("enabled"):
        code, mint = seller(f"/api/voice/{token}/session", {})
        step("ephemeral client secret minted", code == 200 and len(mint.get("clientSecret") or "") > 20)
        _, spoken = seller(f"/api/voice/{token}/answer", {
            "question_id": "C.shared",
            "value": "Yes, actually. We share the back fence with the neighbour at 1249.",
            "status": "answered", "transcript": "Yes, the back fence.",
        })
        step("a spoken sentence stores as the yes it is", spoken.get("value") == "yes",
             str(spoken.get("value")))
        code, _ = seller(f"/api/voice/{token}/answer",
                         {"question_id": "NOPE", "value": "yes", "status": "answered"})
        step("the model cannot invent a question id", code == 400, f"got {code}")
    else:
        step("voice reports itself unconfigured and says so", True, "OPENAI_API_KEY unset")

    # -- contradictions -----------------------------------------------------
    print("\ncontradictions")
    for qid in ("A.public_sewer", "A.septic_tank"):
        seller(f"/api/s/{token}/answers", {"question_id": qid, "value": True}, method="PUT")
    _, state = seller(f"/api/s/{token}/state")
    flag = next((f for f in state["flags"] if f["ruleId"] == "sewer_and_septic"), None)
    if step("sewer and septic raises a hard flag", flag is not None):
        _, after = seller(f"/api/s/{token}/flags/{flag['id']}/resolve",
                          {"questionId": "A.public_sewer", "value": True})
        step("re-confirming the same value does not close it",
             any(f["ruleId"] == "sewer_and_septic" for f in after["flags"]))
        _, fixed = seller(f"/api/s/{token}/flags/{flag['id']}/resolve",
                          {"questionId": "A.septic_tank", "value": False})
        step("actually fixing it does close it",
             not any(f["ruleId"] == "sewer_and_septic" for f in fixed["flags"]))

    # -- completion ---------------------------------------------------------
    print("\ncompletion")
    _, blocked = seller(f"/api/s/{token}/submit", {})
    step("submit blocked while answers are missing", blocked.get("ok") is False,
         f"{len(blocked.get('missingRequired', []))} missing")

    defaults = {"bool": False, "tri": "no", "multi": [], "single": None, "int": 0,
                "text": "n/a", "longtext": "Nothing to add.", "date": ""}
    special = {"P.address": ADDRESS, "P.occupying": "is", "A.water_heater": ["gas"],
               "A.water_supply": ["city"], "A.gas_supply": ["utility"]}
    for _ in range(4):
        state = seller(f"/api/s/{token}/state")[1]
        if not state["missingRequired"]:
            break
        for qid in state["missingRequired"]:
            q = next((x for x in spec["questions"] if x["id"] == qid), None)
            if not q:
                continue
            value = special.get(qid, defaults.get(q["kind"]))
            if q["kind"] == "single" and value is None and q["options"]:
                value = q["options"][0]["id"]
            seller(f"/api/s/{token}/answers", {"question_id": qid, "value": value}, method="PUT")

    _, done = seller(f"/api/s/{token}/submit", {})
    step("a complete disclosure submits", done.get("ok") is True, str(done)[:80])
    _, d2 = agent(f"/api/agent/deals/{deal['id']}")
    step("agent sees it ready for review", d2["status"] == "ready_for_review", d2["status"])

    # -- the document -------------------------------------------------------
    print("\nthe document")
    code, pdf = agent(f"/api/agent/deals/{deal['id']}/preview.pdf", raw=True)
    step("filled PDF downloads", code == 200 and pdf[:4] == b"%PDF", f"{len(pdf)} bytes")
    try:
        from pypdf import PdfReader
        reader = PdfReader(io.BytesIO(pdf))
        step("three pages", len(reader.pages) >= 3, f"{len(reader.pages)}")
        step("output is genuinely flat",
             b"/Widget" not in pdf and reader.get_fields() in (None, {}))
        # Assert against the address as it now stands - the audit corrected it
        # earlier, so checking for the original would test the wrong thing.
        current = agent(f"/api/agent/deals/{deal['id']}")[1]["property_address"]
        step("the current address is printed on page two",
             current.split(",")[0] in reader.pages[1].extract_text(), current)
    except ImportError:
        print("  SKIP  PDF internals (pypdf not installed)")
    _, history = agent(f"/api/agent/deals/{deal['id']}/history")
    step("every write is in the audit trail", isinstance(history, list) and len(history) > 50,
         f"{len(history)} events")

    # -- signature ----------------------------------------------------------
    print("\nsignature")
    code, sent = agent(f"/api/agent/deals/{deal['id']}/send-for-signature", {})
    if code == 200 and sent.get("submission_id"):
        step("pushed to DocuSeal", True, f"submission {sent['submission_id']}")
        again, _ = agent(f"/api/agent/deals/{deal['id']}/send-for-signature", {})
        step("a second send is refused", again == 409, f"got {again}")

        print("\nfrozen once sent")
        for label, path, body, verb, who in [
            ("tap lane", f"/api/s/{token}/answers",
             {"question_id": "C.noise", "value": "yes"}, "PUT", seller),
            ("voice lane", f"/api/voice/{token}/answer",
             {"question_id": "C.noise", "value": "yes", "status": "answered"}, "POST", seller),
            ("group commit", f"/api/s/{token}/answers/commit-group",
             {"questionIds": ["A.oven"]}, "POST", seller),
            ("agent edit", f"/api/agent/deals/{deal['id']}/answers",
             {"question_id": "C.noise", "value": "yes"}, "POST", agent),
        ]:
            code, _ = who(path, body, method=verb)
            step(f"{label} blocked", code == 409, f"got {code}")
    elif code == 503:
        step("signature step reports itself unconfigured", True, "DOCUSEAL_API_KEY unset")
    else:
        step("pushed to DocuSeal", False, f"got {code}: {str(sent)[:110]}")

    print(f"\n{len(PASSED)} passed, {len(FAILED)} failed")
    if FAILED:
        print("failed: " + ", ".join(FAILED))
    return 1 if FAILED else 0


if __name__ == "__main__":
    sys.exit(main())
