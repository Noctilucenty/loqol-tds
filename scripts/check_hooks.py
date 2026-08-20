#!/usr/bin/env python
"""Fail if a React hook is called after an early return.

This is the rules-of-hooks violation that React reports as minified error #310,
and it is the single bug that has bitten this codebase twice. Both times it
blanked the entire seller flow in production, because a component that returns
early on one render and reaches a hook on the next changes its hook count.

The real tool for this is eslint-plugin-react-hooks. This is a dependency-free
stand-in that catches the specific shape that keeps happening: a `use*(` call
that appears textually after a top-level `return` inside an exported component.
It is deliberately blunt - a false positive costs a code move, a false negative
costs the product.

    python scripts/check_hooks.py
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "web" / "src"

# Any top-level component, exported or not. Splitting only on `export function`
# lumped nested helper components into the previous body and reported their
# perfectly legal hooks as violations.
COMPONENT = re.compile(r"^(?:export )?function ([A-Z]\w*)\s*\(", re.MULTILINE)
EARLY_RETURN = re.compile(r"^  (?:if \(.*\)\s*)?return ", re.MULTILINE)
HOOK = re.compile(r"\buse(?:State|Ref|Effect|Memo|Callback|Context|Reducer)\(")


def check(path: Path) -> list[str]:
    text = path.read_text()
    problems: list[str] = []

    starts = [m.start() for m in COMPONENT.finditer(text)]
    for i, start in enumerate(starts):
        end = starts[i + 1] if i + 1 < len(starts) else len(text)
        body = text[start:end]
        name = COMPONENT.match(body).group(1)  # noqa: safe, body starts at the match

        first_return = EARLY_RETURN.search(body)
        if not first_return:
            continue

        tail = body[first_return.start():]
        for hook in HOOK.finditer(tail):
            line = text[:start].count("\n") + body[: first_return.start() + hook.start()].count("\n") + 1
            problems.append(
                f"{path.relative_to(ROOT)}:{line}: {hook.group(0)[:-1]} in {name}() "
                f"is called after an early return"
            )
    return problems


def main() -> int:
    problems: list[str] = []
    for path in sorted(SRC.rglob("*.tsx")):
        problems.extend(check(path))

    if problems:
        print("rules-of-hooks violations:")
        for p in problems:
            print("  " + p)
        print("\nA hook after a conditional return changes the hook count between")
        print("renders. React throws #310 and the component renders nothing.")
        return 1

    print(f"hooks ok ({len(list(SRC.rglob('*.tsx')))} components checked)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
