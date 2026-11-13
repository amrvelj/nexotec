"""KAN-491, KAN-682 (identAPI test agreement §5, §12): no tracked file carries
a value from a real provider account sheet or one of the specification's own
examples.

This file holds only SHA-256 hashes of those values (KAN-682): it used to
assemble them from pieces, which handed them to any reader of a public
repository. A hash of a short value can still be recovered by someone who
guesses its shape and tries every candidate; what the file no longer does is
show it.

What is looked up, in every tracked text file (migrations included), after
Unicode NFC normalisation and case folding: every word, every word inside a
snake_case or dotted name, every pair of adjacent words (across a line break
too), and two string literals joined by `+` or written side by side.
"""

import hashlib
import itertools
import re
import subprocess
import unicodedata
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]

_SALT = "kan-491:"

# value_hash(value) of the two values from the account sheet.
REAL_ACCOUNT_VALUE_HASHES = frozenset(
    {
        "6abb04b16cd0c9cb9dc679ac3096a8cd746d588409cc3fffe08c6f20300bc857",
        "aeb24027f346d2357252496faef8c22a96e20048023bd26ee7013e8b0ed8577e",
    }
)

# value_hash(value) of the six specification values.
SPECIFICATION_EXAMPLE_HASHES = frozenset(
    {
        "9623479a5e655d7ee232652c594f2c9cf6ee0f6ecbf3382b80313b539280f0e5",
        "0af91a68a8ddb0dffd7a2bb9572d33e227c33ccc49cd6ed199595595cb20e5df",
        "b08c409a7b8375ffe53713d671e62022a7df68896d92f7971cc456267abaf70c",
        "b8928a59a54403b9c2da10d92d4f29f07a5193c9b6e38f38eac5a5b070ee7211",
        "4deb8314cab7ff0632b1e45e6255192b4c338521798f76c5e687b2695959ac16",
        "c8f66ee91bc9ddf526142ffeb2a63ab6221b45b676f3ad6ababbe0f424fc8ef9",
    }
)

_WORD = re.compile(r"\w+")
_PART = re.compile(r"[^\W_]+")
# Two string literals joined by `+`, or side by side (Python's implicit concatenation).
ASSEMBLED = re.compile(r"""(["'])([\w ]+)\1\s*\+?\s*(["'])([\w ]+)\3""")


def _fold(text: str) -> str:
    return unicodedata.normalize("NFC", text).casefold()


def value_hash(value: str) -> str:
    return hashlib.sha256((_SALT + _fold(value)).encode("utf-8")).hexdigest()


def guarded_hits(text: str, hashes: frozenset[str]) -> list[int]:
    """Line numbers (1-based) holding a guarded value in one of the forms the module docstring names."""
    seen: dict[str, bool] = {}

    def hit(candidate: str) -> bool:
        if candidate not in seen:
            seen[candidate] = value_hash(candidate) in hashes
        return seen[candidate]

    lines: set[int] = set()
    parts: list[tuple[int, str]] = []  # every word part of the file, in order, with its line
    for number, line in enumerate(_fold(text).splitlines(), start=1):
        words = _WORD.findall(line)
        line_parts = _PART.findall(line)
        joined = [" ".join((a + b).split()) for _, a, _, b in ASSEMBLED.findall(line)]
        if any(hit(w) for w in itertools.chain(words, line_parts, joined)):
            lines.add(number)
        parts += [(number, p) for p in line_parts]
    for (number, a), (_, b) in itertools.pairwise(parts):
        if hit(f"{a} {b}"):
            lines.add(number)
    return sorted(lines)


def _tracked_text_files() -> list[Path]:
    out = subprocess.run(["git", "ls-files"], cwd=ROOT, capture_output=True, text=True, check=True).stdout
    paths = []
    for name in out.splitlines():
        if name.startswith("frontend/node_modules") or name.endswith((".png", ".jpg", ".pdf")):
            continue
        path = ROOT / name
        if path.is_file():
            paths.append(path)
    return paths


def _hits_in_tracked_files(hashes: frozenset[str]) -> list[str]:
    hits = []
    for path in _tracked_text_files():
        try:
            text = path.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            continue
        hits += [f"{path.relative_to(ROOT)}:{number}" for number in guarded_hits(text, hashes)]
    return hits


def test_no_tracked_file_carries_a_real_account_value():
    hits = _hits_in_tracked_files(REAL_ACCOUNT_VALUE_HASHES)
    assert hits == [], "real provider account values in the repository: " + ", ".join(hits)


def test_no_tracked_file_carries_a_specification_example():
    hits = _hits_in_tracked_files(SPECIFICATION_EXAMPLE_HASHES)
    assert hits == [], "specification examples in the repository: " + ", ".join(hits)


def test_the_lookup_finds_every_form_and_nothing_else():
    hashes = frozenset({value_hash("Qzx17"), value_hash("Gelber Kotflügel")})
    assert guarded_hits("plate = 'Qzx17'\n", hashes) == [1]
    assert guarded_hits("PLATE QZX17\n", hashes) == [1]  # case
    assert guarded_hits("fixture_qzx17_ok = 1\n", hashes) == [1]  # inside a snake_case name
    assert guarded_hits("x = ('Qz' + 'x17')\n", hashes) == [1]  # joined by +
    assert guarded_hits("x = 'Qzx' '17'\n", hashes) == [1]  # side by side
    assert guarded_hits("x = 'Gelber ' + 'Kotflügel'\n", hashes) == [1]  # two words, joined by +
    assert guarded_hits("der Gelber Kotflügel links\n", hashes) == [1]
    assert guarded_hits("der Gelber\nKotflügel links\n", hashes) == [1]  # wrapped, NFD
    assert guarded_hits("Qzx170 Qzx1 Gelber\n\nKotflügels\n", hashes) == []


def test_every_hash_is_a_sha256():
    assert len(REAL_ACCOUNT_VALUE_HASHES) == 2
    assert len(SPECIFICATION_EXAMPLE_HASHES) == 6
    assert all(re.fullmatch(r"[0-9a-f]{64}", h) for h in REAL_ACCOUNT_VALUE_HASHES | SPECIFICATION_EXAMPLE_HASHES)
