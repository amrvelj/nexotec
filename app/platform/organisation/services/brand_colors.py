"""The brand colours' contrast check (KAN-205; Dealer Administration PRD,
Logo and brand assets › Colours: "a contrast check on save — a dealer who
picks yellow gets unreadable white text on every document otherwise").

WCAG 2.x AA for normal text: a contrast ratio of at least 4.5:1, the ratio
being (L1 + 0.05) / (L2 + 0.05) of the two colours' relative luminances
(WCAG 2.2, "contrast ratio" and "relative luminance"). Three pairs are
judged, each on its own: the primary against white (white text on a primary
band, and primary text on paper — the ratio is symmetric), the secondary
against white, and the secondary against the primary. A pair with no
secondary colour set is not judged.

The verdict is reported, never enforced here: whether a failing pair blocks
the save is the caller's rule (`services/branding.py`). The SPA computes the
same ratio while the manager picks (`components/branding/contrast.ts`); the
two share their test vectors.
"""

import dataclasses
import re
from typing import Literal

WCAG_AA_NORMAL_TEXT = 4.5
WHITE = "#FFFFFF"
HEX_COLOR = re.compile(r"^#[0-9A-Fa-f]{6}$")

ContrastPairName = Literal["primary_on_white", "secondary_on_white", "secondary_on_primary"]


def _channel(value: int) -> float:
    c = value / 255
    return c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4


def relative_luminance(hex_color: str) -> float:
    if not HEX_COLOR.match(hex_color):
        raise ValueError(f"not a #RRGGBB colour: {hex_color!r}")
    r, g, b = (int(hex_color[i : i + 2], 16) for i in (1, 3, 5))
    return 0.2126 * _channel(r) + 0.7152 * _channel(g) + 0.0722 * _channel(b)


def contrast_ratio(a: str, b: str) -> float:
    la, lb = relative_luminance(a), relative_luminance(b)
    lighter, darker = max(la, lb), min(la, lb)
    return (lighter + 0.05) / (darker + 0.05)


@dataclasses.dataclass(frozen=True)
class ContrastPair:
    pair: ContrastPairName
    foreground: str
    background: str
    ratio: float  # rounded to two decimals, as shown
    passes: bool  # judged on the unrounded ratio


def judge_brand_colors(primary: str, secondary: str | None) -> list[ContrastPair]:
    pairs: list[tuple[ContrastPairName, str, str]] = [("primary_on_white", primary, WHITE)]
    if secondary is not None:
        pairs += [("secondary_on_white", secondary, WHITE), ("secondary_on_primary", secondary, primary)]
    out = []
    for name, fg, bg in pairs:
        ratio = contrast_ratio(fg, bg)
        out.append(
            ContrastPair(
                pair=name,
                foreground=fg.upper(),
                background=bg.upper(),
                ratio=round(ratio, 2),
                passes=ratio >= WCAG_AA_NORMAL_TEXT,
            )
        )
    return out
