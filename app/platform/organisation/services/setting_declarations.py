"""Setting declarations (KAN-805, ADR-214): what an area may store in the
settings store, declared in code by the area that reads it.

A-RULE-0: a module reads configuration and never owns it. So an area above
platform (Aftersales, Parts, Planning …) does not keep a settings table of
its own: it declares each setting here — its key, its kind, what «not set»
does, whether it is a setup-checklist item — and platform stores the values
(`setting_store`). The declarations are registered at start-up by the
composition root `app/setting_registry.py`, which names each area's
registration, so platform imports no area (ADR-131) and nothing registers by
an import's side effect (the message-type registry's pattern, KAN-217).

**Nothing commercial is shipped as a value** (D-A-06): a declaration may say
what «not set» *acts as* for a switch, a number or a choice (O18 «hours»), never
for an amount or a text. «Not set» is a state of its own, not a default value:
the reader is told the setting is not set, and what it acts as.

Keys are strings, `<area>.<name>`; `tests/architecture/test_setting_keys_declared_and_read.py`
fails on a key read and not declared, and on a key declared and read by nobody.
"""

from __future__ import annotations

import enum
import re
from dataclasses import dataclass
from decimal import Decimal

KEY_MAX_LENGTH = 64
TEXT_MAX_LENGTH = 500
TEXT_LANGUAGES = ("de", "fr", "it", "en")

_KEY = re.compile(r"[a-z][a-z0-9_]*\.[a-z][a-z0-9_]*")
_CHOICE = re.compile(r"[a-z][a-z0-9_]*")


class SettingKind(enum.StrEnum):
    SWITCH = "switch"
    NUMBER = "number"
    # A sum of money in CHF, to the Rappen.
    AMOUNT = "amount"
    # One of the declared codes; each code's label is a translation key.
    CHOICE = "choice"
    # A text with one name and a translation per language (ruling 176).
    TEXT = "text"


class WhenNotSet(enum.StrEnum):
    """What «not set» does — said per setting, read by the reader."""

    # Nothing happens: the reader goes on without it (O17: «the proposal is exact»).
    QUIET = "quiet"
    # The reader refuses what needs it, and says which setting is missing (O16).
    REFUSES = "refuses"
    # The reader behaves as if `acts_as` were set (O18: «hours»). Never commercial (D-A-06).
    ACTS_AS = "acts_as"


class SettingDeclarationError(ValueError):
    """A declaration that would make the store wrong — raised at start-up."""


@dataclass(frozen=True)
class SettingDeclaration:
    key: str
    # The entitlement module the area belongs to (`aftersales`, `parts` …);
    # None: every Betrieb has it.
    module: str | None
    kind: SettingKind
    when_not_set: WhenNotSet
    acts_as: bool | Decimal | str | None = None
    # Number and amount bounds; a number's decimals (an amount has two).
    minimum: Decimal | None = None
    maximum: Decimal | None = None
    decimals: int = 0
    # The unit a number is shown in (`minutes`, `per_hour` …), a translation key's last part.
    unit: str | None = None
    choices: tuple[str, ...] = ()
    # An item of the Betrieb's setup checklist, open while the setting is not set
    # (KAN-805 PR-3); `checklist_blocks` names the action it blocks, None: it only
    # notifies (Anto, 2026-11-24).
    checklist_item: bool = False
    checklist_blocks: str | None = None

    @property
    def area(self) -> str:
        return self.key.split(".", 1)[0]


def _check(declaration: SettingDeclaration) -> None:
    key = declaration.key
    if not _KEY.fullmatch(key) or len(key) > KEY_MAX_LENGTH:
        raise SettingDeclarationError(
            f"Setting key {key!r} is not `<area>.<name>` in lower case, at most {KEY_MAX_LENGTH} characters."
        )
    kind = SettingKind(declaration.kind)
    if kind == SettingKind.CHOICE:
        if not declaration.choices or len(set(declaration.choices)) != len(declaration.choices):
            raise SettingDeclarationError(f"Setting {key!r} is a choice without distinct choices.")
        bad = [code for code in declaration.choices if not _CHOICE.fullmatch(code) or len(code) > KEY_MAX_LENGTH]
        if bad:
            raise SettingDeclarationError(f"Setting {key!r}: choice codes {bad} are not lower-case identifiers.")
    elif declaration.choices:
        raise SettingDeclarationError(f"Setting {key!r} is not a choice but declares choices.")
    if kind not in (SettingKind.NUMBER, SettingKind.AMOUNT) and (
        declaration.minimum is not None or declaration.maximum is not None
    ):
        raise SettingDeclarationError(f"Setting {key!r}: only a number or an amount has bounds.")
    if declaration.minimum is not None and declaration.maximum is not None and declaration.minimum > declaration.maximum:
        raise SettingDeclarationError(f"Setting {key!r}: its minimum is above its maximum.")
    if kind == SettingKind.NUMBER and not 0 <= declaration.decimals <= 4:
        raise SettingDeclarationError(f"Setting {key!r}: a number has 0 to 4 decimals.")
    if kind != SettingKind.NUMBER and declaration.decimals:
        raise SettingDeclarationError(f"Setting {key!r}: only a number declares its decimals.")
    if declaration.checklist_blocks is not None and not declaration.checklist_item:
        raise SettingDeclarationError(f"Setting {key!r} blocks an action but is no checklist item.")

    when = WhenNotSet(declaration.when_not_set)
    if when != WhenNotSet.ACTS_AS:
        if declaration.acts_as is not None:
            raise SettingDeclarationError(f"Setting {key!r} names what «not set» acts as, but does not act as it.")
        return
    if kind in (SettingKind.AMOUNT, SettingKind.TEXT):
        raise SettingDeclarationError(
            f"Setting {key!r}: an amount or a text never acts as a shipped value when not set (D-A-06)."
        )
    try:
        validate_value(declaration, declaration.acts_as)
    except SettingValueError as exc:
        raise SettingDeclarationError(f"Setting {key!r}: what «not set» acts as is no valid value: {exc}") from exc


class SettingValueError(ValueError):
    """A value that does not fit its declaration; `reason` is the refusal code."""

    def __init__(self, message: str, *, reason: str) -> None:
        super().__init__(message)
        self.reason = reason


@dataclass(frozen=True)
class TextValue:
    """A text setting's value: one name, and a translation per language where
    one is given (ruling 176). Without one, the name is shown as typed."""

    name: str
    translations: tuple[tuple[str, str], ...] = ()

    def in_language(self, language: str) -> str:
        return dict(self.translations).get(language, self.name)


def validate_value(declaration: SettingDeclaration, value: object) -> bool | Decimal | str | TextValue:
    """The value, checked against the declaration; refused with a reason code."""

    kind = SettingKind(declaration.kind)
    key = declaration.key
    if kind == SettingKind.SWITCH:
        if not isinstance(value, bool):
            raise SettingValueError(f"{key} takes on or off.", reason="setting_value_not_a_switch")
        return value
    if kind in (SettingKind.NUMBER, SettingKind.AMOUNT):
        if isinstance(value, bool) or not isinstance(value, Decimal) or not value.is_finite():
            raise SettingValueError(f"{key} takes a number.", reason="setting_value_not_a_number")
        places = 2 if kind == SettingKind.AMOUNT else declaration.decimals
        exponent = value.normalize().as_tuple().exponent
        if isinstance(exponent, int) and -exponent > places:
            raise SettingValueError(f"{key} takes at most {places} decimals.", reason="setting_value_too_many_decimals")
        if declaration.minimum is not None and value < declaration.minimum:
            raise SettingValueError(f"{key} is at least {declaration.minimum}.", reason="setting_value_below_minimum")
        if declaration.maximum is not None and value > declaration.maximum:
            raise SettingValueError(f"{key} is at most {declaration.maximum}.", reason="setting_value_above_maximum")
        return value
    if kind == SettingKind.CHOICE:
        if value not in declaration.choices:
            raise SettingValueError(f"{key} takes one of {list(declaration.choices)}.", reason="setting_value_not_a_choice")
        assert isinstance(value, str)
        return value
    if not isinstance(value, TextValue):
        raise SettingValueError(f"{key} takes a text.", reason="setting_value_not_a_text")
    name = value.name.strip()
    if not name or len(name) > TEXT_MAX_LENGTH:
        raise SettingValueError(
            f"{key} takes a name of 1 to {TEXT_MAX_LENGTH} characters.", reason="setting_value_text_length"
        )
    translations = []
    for language, text in sorted(dict(value.translations).items()):
        if language not in TEXT_LANGUAGES:
            raise SettingValueError(f"{key}: {language!r} is no language of Nexotec.", reason="setting_value_language")
        text = text.strip()
        if len(text) > TEXT_MAX_LENGTH:
            raise SettingValueError(f"{key}: a translation is at most {TEXT_MAX_LENGTH} characters.", reason="setting_value_text_length")
        if text:
            translations.append((language, text))
    return TextValue(name=name, translations=tuple(translations))


_REGISTRY: dict[str, SettingDeclaration] = {}


def register_setting(declaration: SettingDeclaration) -> None:
    """Adds a declaration; the composition root calls it for each area at
    start-up. The same declaration again is a no-op; another one under the
    same key is refused."""

    _check(declaration)
    existing = _REGISTRY.get(declaration.key)
    if existing is not None:
        if existing == declaration:
            return
        raise SettingDeclarationError(f"Setting {declaration.key!r} is already declared otherwise.")
    _REGISTRY[declaration.key] = declaration


def unregister_setting(key: str) -> None:
    """Tests only: removes a sample declaration again."""

    _REGISTRY.pop(key, None)


def declared_settings() -> tuple[SettingDeclaration, ...]:
    """Every declaration, ordered by key."""

    return tuple(_REGISTRY[key] for key in sorted(_REGISTRY))


def get_declaration(key: str) -> SettingDeclaration | None:
    return _REGISTRY.get(key)
