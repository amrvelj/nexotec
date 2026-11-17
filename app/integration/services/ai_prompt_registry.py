"""The closed registry of AI prompt codes and their placeholders, and the
prompt renderer (KAN-227, D-A-33).

- **Closed, like the standard texts' placeholders** (`platform/documents/services/
  standard_text_placeholders.py`, A-RULE-4): a code is one feature that calls
  `integration.public.generate`, and lists the `{{ token }}` names its caller
  supplies. A prompt using a token its code does not list, or a stray `{{` /
  `}}`, is refused on save (422 `ai_prompt_placeholder_invalid`).
- **A value the caller has not got is said, not guessed.** Unlike a printed
  text, a prompt with one fact missing (a car without a colour on file) is
  still worth sending, so an empty value renders as the language's «keine
  Angabe» — and every seed tells the model to use only the facts given. A
  token the code does not list still fails the render (422
  `ai_prompt_placeholder_unresolved`), never reaching the provider.
- **Only the tenant's own request.** The variables are plain text in the user
  message; the model gets no tool to fetch more (licence §3).
"""

import re
from collections.abc import Mapping
from dataclasses import dataclass

from app.core.errors import UnprocessableEntityError
from app.core.i18n import SwissLanguage

PLACEHOLDER_INVALID = "ai_prompt_placeholder_invalid"
PLACEHOLDER_UNRESOLVED = "ai_prompt_placeholder_unresolved"

VEHICLE_LISTING = "vehicle_listing"


@dataclass(frozen=True)
class PromptCode:
    code: str
    placeholders: tuple[str, ...]
    # The answer's length bound sent to the provider (its tokens, not ours).
    max_output_tokens: int


REGISTRY: tuple[PromptCode, ...] = (
    PromptCode(
        code=VEHICLE_LISTING,
        placeholders=(
            "vehicle.label",
            "vehicle.condition",
            "vehicle.body_style",
            "vehicle.exterior_colour",
            "vehicle.odometer_km",
            "vehicle.first_registration",
            "vehicle.price",
            "vehicle.equipment",
        ),
        max_output_tokens=800,
    ),
)

_BY_CODE = {entry.code: entry for entry in REGISTRY}

NOT_STATED: dict[SwissLanguage, str] = {
    SwissLanguage.DE: "keine Angabe",
    SwissLanguage.FR: "non indiqué",
    SwissLanguage.IT: "non indicato",
    SwissLanguage.EN: "not stated",
}

# One well-formed token: `{{ name }}`, whitespace inside the braces allowed —
# the standard texts' syntax.
_TOKEN = re.compile(r"\{\{\s*([^{}]*?)\s*\}\}")


def prompt_code(code: str) -> PromptCode | None:
    return _BY_CODE.get(code)


def require_code(code: str) -> PromptCode:
    entry = prompt_code(code)
    if entry is None:
        raise UnprocessableEntityError(f"No AI prompt {code} exists.", details={"reason": "ai_prompt_code_unknown", "code": code})
    return entry


def _problems(text: str, entry: PromptCode) -> list[dict[str, str]]:
    found: list[dict[str, str]] = []
    for name in dict.fromkeys(match.group(1) for match in _TOKEN.finditer(text)):
        if name not in entry.placeholders:
            found.append({"token": name, "problem": "unknown"})
    leftover = _TOKEN.sub("", text)
    if "{{" in leftover or "}}" in leftover:
        found.append({"token": "", "problem": "malformed"})
    return found


def validate_prompt(entry: PromptCode, *, system_prompt: str, user_prompt: str) -> None:
    problems = [
        {**problem, "field": field}
        for field, text in (("systemPrompt", system_prompt), ("userPrompt", user_prompt))
        for problem in _problems(text, entry)
    ]
    if problems:
        raise UnprocessableEntityError(
            "The prompt uses placeholders that are not allowed here.",
            details={"reason": PLACEHOLDER_INVALID, "placeholders": problems},
        )
    for field, text in (("systemPrompt", system_prompt), ("userPrompt", user_prompt)):
        if not text.strip():
            raise UnprocessableEntityError(
                "A prompt needs both instructions and a request.", details={"reason": "ai_prompt_empty", "field": field}
            )


def render(entry: PromptCode, text: str, *, values: Mapping[str, str | None], language: SwissLanguage) -> str:
    problems = _problems(text, entry)
    if problems:
        raise UnprocessableEntityError(
            "The AI prompt could not be rendered: a placeholder cannot be resolved.",
            details={"reason": PLACEHOLDER_UNRESOLVED, "placeholders": problems},
        )

    def value_of(match: re.Match[str]) -> str:
        value = values.get(match.group(1))
        return str(value).strip() if value is not None and str(value).strip() else NOT_STATED[language]

    return _TOKEN.sub(value_of, text)
