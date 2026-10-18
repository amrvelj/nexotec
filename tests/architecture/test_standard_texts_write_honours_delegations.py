"""KAN-199 (FR-A-28, D-A-23): the standard-texts write row is "manager, plus a
user holding a delegation for that category". An endpoint that guards a
standard-text write with plain `require_write("standard_texts")` would
silently ignore every delegation, and so would `require_write_or_delegated`
on a route whose path carries no `{category}` segment (the gate reads the
category from the path only). Scans every router module (app/**/api/) for
both, across line breaks.
"""

import re
from pathlib import Path

_APP = Path(__file__).resolve().parent.parent.parent / "app"
_PLAIN_WRITE = re.compile(r"""require_write\(\s*["']standard_texts["']""")
_DELEGATED = re.compile(r"""require_write_or_delegated\(\s*["']standard_texts["']""")
_ROUTE = re.compile(r"""@\w+\.(?:get|put|post|patch|delete)\(\s*["']([^"']+)["']""")


def _offenders(source: str) -> list[str]:
    found = [f"plain require_write at offset {m.start()}" for m in _PLAIN_WRITE.finditer(source)]
    routes = [(m.start(), m.group(1)) for m in _ROUTE.finditer(source)]
    for m in _DELEGATED.finditer(source):
        before = [path for pos, path in routes if pos < m.start()]
        path = before[-1] if before else None
        if path is None or "{category}" not in path:
            found.append(f"delegated gate on a route without {{category}}: {path!r}")
    return found


def test_no_standard_text_write_bypasses_the_delegation_gate():
    offenders = [
        f"{path.relative_to(_APP.parent)}: {problem}"
        for path in sorted(_APP.rglob("api/**/*.py"))  # gates are attached in routers; docstrings elsewhere name them
        for problem in _offenders(path.read_text())
    ]
    assert offenders == [], offenders


def test_the_scan_catches_both_forms():
    plain = """
@router.put("/standard-texts/{category}")
def write(principal=Depends(require_write(
    "standard_texts"))):
    ...
"""
    assert len(_offenders(plain)) == 1
    pathless = """
@router.put("/standard-texts/{text_id}")
def write(principal=Depends(require_write_or_delegated("standard_texts"))):
    ...
"""
    assert len(_offenders(pathless)) == 1
    sound = """
@router.put("/standard-texts/{category}/{text_id}")
def write(principal=Depends(require_write_or_delegated("standard_texts"))):
    ...
"""
    assert _offenders(sound) == []
