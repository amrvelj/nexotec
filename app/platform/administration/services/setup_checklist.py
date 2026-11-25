"""The setup checklist (KAN-214; Dealer Administration D-A-21, FR-A-26,
FR-A-34; Anto, 2026-11-13, Q2 A; 2026-11-14, Q5 A).

**Derived, never stored.** Every read computes each item from the owner's
own data; nothing is ticked. An item applies to a Betrieb by its
entitlements (Q2 A) and blocks two things:

- **before activation, activation itself** — a Betrieb becomes `active` only
  when every applicable item is complete (D-A-21: «goes live complete»);
- **after activation, its own action** (`blocks`) — a prerequisite broken
  later (an IBAN deleted, a location's hours removed) refuses only that
  action, with a refusal naming the item and linking to it (FR-A-26,
  `require_item_for`), and the daily job raises one `administration`
  notification per regression (`services/setup_checklist_regressions.py`).

The items (Q2 A — the PRD's table, read per entitlement):

| item | applies | complete when | after activation blocks |
|---|---|---|---|
| `branding` | every Betrieb | the primary logo is uploaded (the brand colour always holds a value — the shipped default until the manager changes it) | nothing (activation only) |
| `vat_and_rounding` | every Betrieb | the VAT rate is set and, when VAT-liable, the VAT number; cash rounding is set (it has a shipped default) | issuing an invoice |
| `bank_account` | every Betrieb | a default bank account exists (its IBAN / QR-IBAN is validated on save) | issuing an invoice |
| `number_ranges` | every Betrieb | the manager confirmed the number ranges once (`number_range_confirmation`) | issuing a numbered document — never in practice: a confirmation is a fact and activation freezes every range |
| `location_opening_hours` | `sales` | an active location has opening hours | marketplace publishing |
| `users` | every Betrieb | an active manager exists — the manager alone meets it in a one-person Betrieb | nothing (activation only) |
| `labour_rates` | `aftersales` | an active category has a rate, not withdrawn, at an active location — one starting later counts (KAN-806; Anto, 2026-11-25) | nothing yet (activation only; an order prices with it from KAN-810) |

«Integrations connected» is never an item: an integration blocks only the
feature it entitles (Q2 A).

**A setting of the settings store** (KAN-805 PR-3, ADR-214) is an item when its
declaration says so: its code is the setting's key (`aftersales.time_unit`),
it applies with the setting's module, its link is the area's settings screen,
and it is complete while the Betrieb's own value is in force
(`setting_store.is_set_for_dealership`; a location's value overrides it, never
stands in for it — a read without a location would still answer «not set»).
It blocks the action its declaration names, or none: a setting declared after a Betrieb's activation only notifies
(the daily regression job) unless its declaration names an action (Anto,
2026-11-24). The publishing profile joins when it ships, as a
contribution through `app.core.setup_checklist` (Q5 A) — items owned above
platform are read through that port, never by calling up the layers.
"""

import dataclasses
import enum
import uuid
from collections.abc import Callable

from sqlalchemy import exists, select
from sqlalchemy.orm import Session

from app.core.errors import ConflictError
from app.core.permissions import ModuleCode
from app.core.setup_checklist import ChecklistContribution, checklist_contributions
from app.platform.identity.models.dealership_membership import DealershipMembership
from app.platform.identity.models.user import User, UserStatus
from app.platform.identity.services.user_common import membership_is_active
from app.platform.organisation.models.branding_asset import BrandingAsset, BrandingAssetKind
from app.platform.organisation.models.dealership import Dealership, DealershipStatus, Location
from app.platform.organisation.models.dealership_bank_account import DealershipBankAccount
from app.platform.organisation.models.dealership_settings import DealershipSettings
from app.platform.organisation.models.location_schedule import LocationOpeningHours
from app.platform.organisation.models.number_range import NumberRangeConfirmation
from app.platform.organisation.services import setting_store
from app.platform.organisation.services.entitlement import list_active_modules
from app.platform.organisation.services.labour_rates import has_rate_ready
from app.platform.organisation.services.setting_declarations import declared_settings


class BlockedAction(str, enum.Enum):
    """What an incomplete item refuses once the Betrieb is active."""

    INVOICE_ISSUE = "invoice_issue"
    NUMBERED_DOCUMENT = "numbered_document"
    MARKETPLACE_PUBLISHING = "marketplace_publishing"


class ChecklistItemCode(str, enum.Enum):
    BRANDING = "branding"
    VAT_AND_ROUNDING = "vat_and_rounding"
    BANK_ACCOUNT = "bank_account"
    NUMBER_RANGES = "number_ranges"
    LOCATION_OPENING_HOURS = "location_opening_hours"
    USERS = "users"
    LABOUR_RATES = "labour_rates"


@dataclasses.dataclass(frozen=True)
class _ItemDefinition:
    code: str
    # Empty: every Betrieb. Otherwise the item applies when the Betrieb
    # holds one of these modules.
    applies_to_modules: frozenset[str]
    blocks: str | None
    link: str
    is_complete: Callable[[Session, Dealership], bool]


@dataclasses.dataclass(frozen=True)
class ChecklistItem:
    code: str
    complete: bool
    # The action the item refuses after activation; None = activation only.
    blocks: str | None
    link: str


@dataclasses.dataclass(frozen=True)
class SetupChecklist:
    dealership_id: uuid.UUID
    status: DealershipStatus
    items: tuple[ChecklistItem, ...]

    @property
    def complete(self) -> bool:
        return all(item.complete for item in self.items)

    @property
    def missing(self) -> tuple[ChecklistItem, ...]:
        return tuple(item for item in self.items if not item.complete)


# --- the completion rules, each read from the owner's data -------------------------------------


def _branding_complete(db: Session, dealership: Dealership) -> bool:
    has_logo = db.scalar(
        select(
            exists().where(
                BrandingAsset.dealership_id == dealership.id,
                BrandingAsset.location_id.is_(None),
                BrandingAsset.kind == BrandingAssetKind.PRIMARY,
            )
        )
    )
    return bool(has_logo) and bool(dealership.brand_primary_color)


def _vat_complete(db: Session, dealership: Dealership) -> bool:
    if dealership.vat_rate is None:
        return False
    if dealership.vat_liable and not (dealership.vat_number or "").strip():
        return False
    rounding = db.scalar(
        select(DealershipSettings.cash_rounding).where(DealershipSettings.dealership_id == dealership.id)
    )
    return rounding is not None


def _bank_account_complete(db: Session, dealership: Dealership) -> bool:
    return bool(
        db.scalar(
            select(
                exists().where(
                    DealershipBankAccount.dealership_id == dealership.id,
                    DealershipBankAccount.is_default.is_(True),
                )
            )
        )
    )


def _number_ranges_complete(db: Session, dealership: Dealership) -> bool:
    return bool(db.scalar(select(exists().where(NumberRangeConfirmation.dealership_id == dealership.id))))


def _opening_hours_complete(db: Session, dealership: Dealership) -> bool:
    return bool(
        db.scalar(
            select(
                exists()
                .where(LocationOpeningHours.location_id == Location.id)
                .where(Location.tenant_id == dealership.id, Location.is_active.is_(True))
            )
        )
    )


def _users_complete(db: Session, dealership: Dealership) -> bool:
    home_manager = db.scalar(
        select(
            exists().where(
                User.tenant_id == dealership.id,
                User.is_dealer_manager.is_(True),
                User.status == UserStatus.ACTIVE,
            )
        )
    )
    if home_manager:
        return True
    member_manager = db.scalar(
        select(
            exists()
            .where(
                DealershipMembership.dealership_id == dealership.id,
                DealershipMembership.is_dealer_manager.is_(True),
                membership_is_active(),
            )
            .where(User.id == DealershipMembership.user_id, User.status == UserStatus.ACTIVE)
        )
    )
    return bool(member_manager)


_ALL: frozenset[str] = frozenset()

ITEMS: tuple[_ItemDefinition, ...] = (
    _ItemDefinition(ChecklistItemCode.BRANDING.value, _ALL, None, "/administration/branding", _branding_complete),
    _ItemDefinition(
        ChecklistItemCode.VAT_AND_ROUNDING.value,
        _ALL,
        BlockedAction.INVOICE_ISSUE.value,
        "/administration/business",
        _vat_complete,
    ),
    _ItemDefinition(
        ChecklistItemCode.BANK_ACCOUNT.value,
        _ALL,
        BlockedAction.INVOICE_ISSUE.value,
        "/administration/business",
        _bank_account_complete,
    ),
    _ItemDefinition(
        ChecklistItemCode.NUMBER_RANGES.value,
        _ALL,
        BlockedAction.NUMBERED_DOCUMENT.value,
        "/administration/number-ranges",
        _number_ranges_complete,
    ),
    _ItemDefinition(
        ChecklistItemCode.LOCATION_OPENING_HOURS.value,
        frozenset({ModuleCode.SALES.value}),
        BlockedAction.MARKETPLACE_PUBLISHING.value,
        "/administration/locations",
        _opening_hours_complete,
    ),
    _ItemDefinition(ChecklistItemCode.USERS.value, _ALL, None, "/administration/users", _users_complete),
    # KAN-806 (slice 1, section 5): at least one category with a rate.
    _ItemDefinition(
        ChecklistItemCode.LABOUR_RATES.value,
        frozenset({ModuleCode.AFTERSALES.value}),
        None,
        "/administration/labour-rates",
        lambda db, dealership: has_rate_ready(db, dealership_id=dealership.id),
    ),
)


def _contributed_rule(contribution: ChecklistContribution) -> Callable[[Session, Dealership], bool]:
    def rule(db: Session, dealership: Dealership) -> bool:
        return contribution.is_complete(db, dealership.id)

    return rule


def _setting_rule(key: str) -> Callable[[Session, Dealership], bool]:
    def rule(db: Session, dealership: Dealership) -> bool:
        return setting_store.is_set_for_dealership(db, dealership_id=dealership.id, key=key)

    return rule


def _setting_items() -> tuple[_ItemDefinition, ...]:
    items = []
    for declaration in declared_settings():
        if not declaration.checklist_item:
            continue
        if declaration.checklist_blocks is not None and declaration.checklist_blocks not in _BLOCKED_ACTIONS:
            raise ValueError(
                f"Setting {declaration.key!r} blocks {declaration.checklist_blocks!r}, no action of the checklist."
            )
        items.append(
            _ItemDefinition(
                code=declaration.key,
                applies_to_modules=frozenset({declaration.module}) if declaration.module else _ALL,
                blocks=declaration.checklist_blocks,
                link=f"/administration/settings/{declaration.area}",
                is_complete=_setting_rule(declaration.key),
            )
        )
    return tuple(items)


_BLOCKED_ACTIONS = frozenset(action.value for action in BlockedAction)


def _definitions() -> tuple[_ItemDefinition, ...]:
    own = {item.code for item in ITEMS}
    contributed = tuple(
        _ItemDefinition(
            code=contribution.code,
            applies_to_modules=contribution.applies_to_modules,
            blocks=contribution.blocks,
            link=contribution.link,
            is_complete=_contributed_rule(contribution),
        )
        for contribution in checklist_contributions()
        if contribution.code not in own
    )
    return ITEMS + contributed + _setting_items()


def _applies(definition: _ItemDefinition, modules: frozenset[str]) -> bool:
    return not definition.applies_to_modules or bool(definition.applies_to_modules & modules)


def derive(db: Session, dealership: Dealership) -> SetupChecklist:
    """The Betrieb's checklist as it stands — only the items that apply."""

    modules = list_active_modules(db, dealership.id)
    items = tuple(
        ChecklistItem(
            code=definition.code,
            complete=definition.is_complete(db, dealership),
            blocks=definition.blocks,
            link=definition.link,
        )
        for definition in _definitions()
        if _applies(definition, modules)
    )
    return SetupChecklist(dealership_id=dealership.id, status=dealership.status, items=items)


def judged_against_checklist(dealership: Dealership) -> bool:
    """An active Betrieb is judged against its checklist only when it was
    activated by the procedure (`activated_at` set). A Betrieb the KAN-214
    migration set active was trading before the checklist existed: nothing it
    does today is refused, and no regression is raised for an item it never
    completed, until a ruling brings it under the checklist."""

    return not (dealership.status == DealershipStatus.ACTIVE and dealership.activated_at is None)


def blocking_item(db: Session, dealership_id: uuid.UUID, action: BlockedAction | str) -> ChecklistItem | None:
    """The first incomplete item that blocks `action` for the Betrieb, or
    None. Only what applies to the Betrieb's entitlements is judged."""

    dealership = db.get(Dealership, dealership_id)
    if dealership is None or not judged_against_checklist(dealership):
        return None
    wanted = action.value if isinstance(action, BlockedAction) else action
    for item in derive(db, dealership).items:
        if item.blocks == wanted and not item.complete:
            return item
    return None


def require_item_for(db: Session, dealership_id: uuid.UUID, action: BlockedAction | str) -> None:
    """FR-A-26: refuse `action` while an item it needs is incomplete — 409
    `setup_checklist_item_missing`, naming the item and where to fix it.
    Never "configuration incomplete"."""

    item = blocking_item(db, dealership_id, action)
    if item is not None:
        raise ConflictError(
            "A prerequisite from the setup checklist is missing.",
            details={
                "reason": "setup_checklist_item_missing",
                "item": item.code,
                "link": item.link,
                "action": action.value if isinstance(action, BlockedAction) else action,
            },
        )
