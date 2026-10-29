"""KAN-491 (identAPI test agreement §5, §12): no fixture, mock or doc in
the repository carries a value from a real provider account sheet, and the
mock adapter carries none of the specification's own examples.

Before KAN-491, tests/test_integration_auto_i_dat_account.py used a real
project label from a dealer's account sheet as BenutzerInfo, and the
mock's ZH123456 plate answered with the specification's KontrollschildInfo
example. The values are assembled from pieces below and named nowhere
else, so this file does not carry them either. The SOAP adapter's spec-derived sample XML in
tests/test_integration_soap_adapter.py may keep spec values (criterion 4);
the mock may not.
"""

import re
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]

# Values from the real account sheet (assembled, so this file does not
# match itself).
REAL_ACCOUNT_VALUES = ("DC" + "Mi", "ef" + "dealer")

# The specification's KontrollschildInfo example and its OptKeys.
SPEC_EXAMPLES_IN_THE_MOCK = (
    "1SC" + "653", "6267" + "02193", "1003" + "69", "1000" + "17", "1081" + "81",
    "Höhenverstellbarer " + "Beifahrersitz",
)
MOCK = ROOT / "app" / "integration" / "adapters" / "auto_i_dat_mock.py"


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


def test_no_tracked_file_carries_a_real_account_value():
    pattern = re.compile(r"\b(" + "|".join(map(re.escape, REAL_ACCOUNT_VALUES)) + r")\b")
    hits = []
    for path in _tracked_text_files():
        try:
            text = path.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            continue
        for number, line in enumerate(text.splitlines(), start=1):
            if pattern.search(line):
                hits.append(f"{path.relative_to(ROOT)}:{number}")
    assert hits == [], "real provider account values in the repository: " + ", ".join(hits)


def test_the_mock_adapter_carries_no_specification_example():
    text = MOCK.read_text(encoding="utf-8")
    found = [value for value in SPEC_EXAMPLES_IN_THE_MOCK if re.search(rf"\b{value}\b", text)]
    assert found == []
