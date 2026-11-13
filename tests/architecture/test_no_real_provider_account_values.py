"""KAN-491, KAN-682 (identAPI test agreement §5, §12): no tracked file carries
a value from a real provider account sheet or one of the specification's own
examples.

Before KAN-491, tests/test_integration_auto_i_dat_account.py used a real
project label from a dealer's account sheet, and the mock adapter and
tests/test_vehicle_identification.py answered with the specification's
KontrollschildInfo example and its OptKeys.

This file holds only SHA-256 hashes of those values (KAN-682): it used to
assemble them from pieces, which handed them to any reader of a public
repository. A hash of a short value can still be recovered by someone who
guesses its shape and tries every candidate; what the file no longer does is
show it. Every word, and every pair of adjacent words, of every tracked text
file is hashed and looked up.
"""

import hashlib
import itertools
import re
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]

_SALT = "kan-491:"

# sha256(_SALT + value): the two values from the real account sheet.
REAL_ACCOUNT_VALUE_HASHES = frozenset(
    {
        "a06942b026d167d9e52b29402afb712a38c1b72cc330de6d9b13e82e2e6f923e",
        "aeb24027f346d2357252496faef8c22a96e20048023bd26ee7013e8b0ed8577e",
    }
)

# sha256(_SALT + value): the specification's KontrollschildInfo example, its
# OptKeys and one option text (two words).
SPECIFICATION_EXAMPLE_HASHES = frozenset(
    {
        "7bf7a2edac65949f59ed5ffc383e7be4a1d6dff3872354d76d27303f091ca69e",
        "0af91a68a8ddb0dffd7a2bb9572d33e227c33ccc49cd6ed199595595cb20e5df",
        "b08c409a7b8375ffe53713d671e62022a7df68896d92f7971cc456267abaf70c",
        "b8928a59a54403b9c2da10d92d4f29f07a5193c9b6e38f38eac5a5b070ee7211",
        "4deb8314cab7ff0632b1e45e6255192b4c338521798f76c5e687b2695959ac16",
        "72df54ea50f0a6170ffd34f9ef50319b0a4bbc16c3a0630d9805f1afa7e1f7c8",
    }
)

_WORD = re.compile(r"\w+")


def value_hash(value: str) -> str:
    return hashlib.sha256((_SALT + value).encode("utf-8")).hexdigest()


def guarded_hits(text: str, hashes: frozenset[str]) -> list[int]:
    """Line numbers (1-based) on which a word or two adjacent words hash into `hashes`."""
    seen: dict[str, bool] = {}

    def hit(candidate: str) -> bool:
        if candidate not in seen:
            seen[candidate] = value_hash(candidate) in hashes
        return seen[candidate]

    lines = []
    for number, line in enumerate(text.splitlines(), start=1):
        words = _WORD.findall(line)
        if any(hit(word) for word in words) or any(hit(f"{a} {b}") for a, b in itertools.pairwise(words)):
            lines.append(number)
    return lines


def _tracked_text_files() -> list[Path]:
    out = subprocess.run(["git", "ls-files"], cwd=ROOT, capture_output=True, text=True, check=True).stdout
    paths = []
    for name in out.splitlines():
        if name.startswith(("frontend/node_modules", "alembic/versions/")) or name.endswith((".png", ".jpg", ".pdf")):
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


def test_the_lookup_finds_a_word_and_a_pair_of_words_and_nothing_else():
    hashes = frozenset({value_hash("Qzx17"), value_hash("Gelber Kotflügel")})
    text = "ok\nplate = 'Qzx17'\nQzx170 is longer\nder Gelber Kotflügel links\nGelber\nKotflügel\n"
    assert guarded_hits(text, hashes) == [2, 4]


def test_the_file_names_no_guarded_value_in_clear():
    # Two account values, six specification values (one of them two words).
    assert len(REAL_ACCOUNT_VALUE_HASHES) == 2
    assert len(SPECIFICATION_EXAMPLE_HASHES) == 6
    assert all(re.fullmatch(r"[0-9a-f]{64}", h) for h in REAL_ACCOUNT_VALUE_HASHES | SPECIFICATION_EXAMPLE_HASHES)
