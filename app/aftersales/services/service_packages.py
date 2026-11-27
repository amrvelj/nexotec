"""The service catalogue (KAN-818, AS1-15; build slice 1, section 3.10;
rulings 174, 176, 182, 183; checks 44, 45, 46).

Table 3.10:

| What happens | Result | Refused when |
| --- | --- | --- |
| New package: a name, labour positions with a fixed time and a labour category, text positions, «check needed» per labour position | Active | The name is empty; a labour position has no time |
| A package is put onto a job | Its positions are copied into the job (`copy_package_onto_job`) | The order is frozen; the job is stopped (KAN-811, as for a typed position) |
| A package is changed or set inactive | New jobs get the new content; an inactive package is not offered. No order that exists changes | – |
| A package gets its name in another language | Read in that language; without a translation, as typed (ruling 176) | – |

**The package is the aggregate:** it carries the `version`, and a change
replaces its name, translations, state and positions in one write with
If-Match. A position sent with its `id` and the same time keeps the unit and
the work units per hour it was typed with (ruling 183, as check 48 rules for
a workshop position); a new or changed time is read in the unit in force for
the Betrieb now (`platform.public.labour_time_unit`, the Betrieb's own
value), and a client that typed it in another unit is refused rather than
relabelled (409 `service_package_time_unit_changed`).

**Names are unique per Betrieb, ignoring case**, as every named list of a
Betrieb is (labour categories, the planner lists), so «copy from a sister
Betrieb» can match a package by its name. The check runs under the
Betrieb's catalogue lock (a transaction advisory lock).

**Copied, never linked** (section 10; check 45): a copied workshop position
names no package; changing a package, or setting it inactive, touches no
order. An inactive package is not offered and cannot be copied (409
`service_package_inactive`). A package is never deleted.

**Pricing on the job** (check 44): a labour position becomes labour by
time with the package's time, in the unit it was typed in, and its rate is
the one «which applies here» — `positions.propose_rate`: an agreement with
the invoice recipient first, else the category rate at the order's location
on the order's day (check 11), keeping its source. No rate for one of the
package's categories refuses the whole copy (409
`service_package_labour_rate_missing`, the category named): a package lands
whole or not at all.

The caller (the API) checks the role, the Betrieb and the module. Every
write is audited; writes commit here.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any

from sqlalchemy import func, select, text, update
from sqlalchemy.orm import Session

from app.aftersales.models.service_order import (
    LabourTimeUnit,
    PositionKind,
    ServiceOrder,
    ServiceOrderJob,
    ServiceOrderPosition,
)
from app.aftersales.models.service_package import (
    CATEGORY_LABEL_LENGTH,
    PACKAGE_NAME_LENGTH,
    POSITION_TEXT_LENGTH,
    PackagePositionKind,
    ServicePackage,
    ServicePackagePosition,
)
from app.aftersales.services import answers
from app.aftersales.services.positions import _snapshot as position_snapshot
from app.aftersales.services.positions import propose_rate
from app.aftersales.services.service_order import ENTITY_TYPE as ORDER_ENTITY_TYPE
from app.aftersales.services.service_order import record_order_change, refuse_frozen
from app.core import configuration_copy as seam
from app.core.audit import record_audit_event
from app.core.base import utcnow
from app.core.concurrency import check_version
from app.core.configuration_copy import CopiedItem
from app.core.errors import ConflictError, NotFoundError, UnprocessableEntityError
from app.core.i18n import SwissLanguage
from app.core.text_collation import TextSortKey
from app.platform.public import (
    get_dealership_or_404,
    labour_categories_of_dealership,
    labour_category_labels,
    labour_time_unit,
)

ENTITY_TYPE = "service_package"
_TIME_PLACES = 2
_LANGUAGES = frozenset(language.value for language in SwissLanguage)


# --- input ------------------------------------------------------------------------------------


@dataclass(frozen=True)
class PackagePositionInput:
    kind: PackagePositionKind
    text: str
    id: uuid.UUID | None = None
    labour_category_id: uuid.UUID | None = None
    time: Decimal | None = None
    # The unit the client typed the time in; absent = the unit in force.
    time_unit: LabourTimeUnit | None = None
    check_needed: bool = False


@dataclass(frozen=True)
class PackageInput:
    name: str
    name_translations: dict[str, str] | None = None
    is_active: bool = True
    positions: list[PackagePositionInput] = field(default_factory=list)


# --- the lock and the reads -------------------------------------------------------------------


def lock_catalogue(db: Session, dealership_id: uuid.UUID) -> None:
    """Serialises the Betrieb's catalogue writes (the name check) until the transaction ends."""

    if db.get_bind().dialect.name != "postgresql":
        return
    digest = hashlib.sha256(f"aftersales.service_catalogue:{dealership_id}".encode()).digest()
    db.execute(text("SELECT pg_advisory_xact_lock(:key)"), {"key": int.from_bytes(digest[:8], "big", signed=True)})


def list_packages(db: Session, *, dealership_id: uuid.UUID, active_only: bool = False) -> list[ServicePackage]:
    stmt = select(ServicePackage).where(ServicePackage.tenant_id == dealership_id)
    if active_only:
        stmt = stmt.where(ServicePackage.is_active.is_(True))
    return list(db.scalars(stmt.order_by(TextSortKey(ServicePackage.name), ServicePackage.id)))


def positions_of(db: Session, package_ids: list[uuid.UUID]) -> dict[uuid.UUID, list[ServicePackagePosition]]:
    out: dict[uuid.UUID, list[ServicePackagePosition]] = {package_id: [] for package_id in package_ids}
    if not package_ids:
        return out
    rows = db.scalars(
        select(ServicePackagePosition)
        .where(ServicePackagePosition.package_id.in_(package_ids))
        .order_by(ServicePackagePosition.package_id, ServicePackagePosition.sort_index)
    )
    for row in rows:
        out[row.package_id].append(row)
    return out


def get_package_or_404(
    db: Session, *, dealership_id: uuid.UUID, package_id: uuid.UUID, lock: bool = False
) -> ServicePackage:
    stmt = select(ServicePackage).where(ServicePackage.id == package_id, ServicePackage.tenant_id == dealership_id)
    if lock:
        stmt = stmt.with_for_update().execution_options(populate_existing=True)
    package = db.scalar(stmt)
    if package is None:
        raise NotFoundError(f"Service package {package_id} was not found.")
    return package


# --- validation -------------------------------------------------------------------------------


def _clean(value: str) -> str:
    return " ".join((value or "").split())


def _refuse(reason: str, message: str, **details: Any) -> UnprocessableEntityError:
    return UnprocessableEntityError(message, details={"reason": reason, **details})


def _clean_name(name: str) -> str:
    cleaned = _clean(name)
    if not cleaned:
        raise _refuse("service_package_name_required", "A service package needs a name.")
    if len(cleaned) > PACKAGE_NAME_LENGTH:
        raise _refuse(
            "service_package_name_too_long", f"A service package's name has at most {PACKAGE_NAME_LENGTH} characters."
        )
    return cleaned


def _clean_translations(translations: dict[str, str] | None) -> dict[str, str] | None:
    """Only the languages given (ruling 176); an empty one is dropped."""

    if not translations:
        return None
    cleaned: dict[str, str] = {}
    for language, value in sorted(translations.items()):
        code = str(getattr(language, "value", language))
        if code not in _LANGUAGES:
            raise _refuse("service_package_name_language", f"{code!r} is no language of Nexotec.")
        name = _clean(value)
        if len(name) > PACKAGE_NAME_LENGTH:
            raise _refuse(
                "service_package_name_too_long",
                f"A service package's name has at most {PACKAGE_NAME_LENGTH} characters.",
            )
        if name:
            cleaned[code] = name
    return cleaned or None


def _places(value: Decimal) -> int:
    exponent = value.as_tuple().exponent
    return -exponent if isinstance(exponent, int) and exponent < 0 else 0


def _check_position(index: int, data: PackagePositionInput) -> str:
    """The shape of one position (table 3.10); returns its cleaned text."""

    position_text = _clean(data.text)
    if not position_text:
        raise _refuse("service_package_position_text_required", "A package position needs its text.", position=index)
    if len(position_text) > POSITION_TEXT_LENGTH:
        raise _refuse(
            "service_package_position_text_too_long",
            f"A package position's text has at most {POSITION_TEXT_LENGTH} characters.",
            position=index,
        )
    if data.kind is PackagePositionKind.TEXT:
        if data.labour_category_id is not None or data.time is not None or data.check_needed:
            raise _refuse(
                "service_package_position_incomplete",
                "A text position carries no labour category, time or «check needed».",
                field="kind",
                position=index,
            )
        return position_text
    if data.time is None:
        raise _refuse("service_package_labour_time_required", "A labour position needs its time.", position=index)
    if data.time <= 0:
        raise _refuse("service_package_labour_time_not_positive", "A labour time is above zero.", position=index)
    if _places(data.time) > _TIME_PLACES:
        raise _refuse(
            "service_package_labour_time_too_many_decimals",
            f"A labour time has at most {_TIME_PLACES} decimals.",
            position=index,
        )
    if data.labour_category_id is None:
        raise _refuse(
            "service_package_labour_category_required", "A labour position names its labour category.", position=index
        )
    return position_text


def _category_labels(db: Session, dealership_id: uuid.UUID, ids: set[uuid.UUID]) -> dict[uuid.UUID, str]:
    labels = labour_category_labels(db, dealership_id=dealership_id, category_ids=sorted(ids))
    unknown = sorted(str(category_id) for category_id in ids if category_id not in labels)
    if unknown:
        raise _refuse(
            "service_package_labour_category_unknown",
            "This labour category is not one of this business's.",
            labourCategoryIds=unknown,
        )
    return {category_id: label.name[:CATEGORY_LABEL_LENGTH] for category_id, label in labels.items()}


def _name_taken(db: Session, dealership_id: uuid.UUID, name: str, *, other_than: uuid.UUID | None) -> bool:
    stmt = select(ServicePackage.id).where(
        ServicePackage.tenant_id == dealership_id, func.lower(ServicePackage.name) == name.lower()
    )
    if other_than is not None:
        stmt = stmt.where(ServicePackage.id != other_than)
    return db.scalar(stmt.limit(1)) is not None


# --- snapshots --------------------------------------------------------------------------------


def _position_snapshot(position: ServicePackagePosition) -> dict[str, Any]:
    return {
        "kind": position.kind.value,
        "text": position.text,
        "labourCategoryId": str(position.labour_category_id) if position.labour_category_id else None,
        "time": str(position.time_quantity) if position.time_quantity is not None else None,
        "timeUnit": position.time_unit.value if position.time_unit else None,
        "workUnitsPerHour": str(position.work_units_per_hour) if position.work_units_per_hour is not None else None,
        "checkNeeded": position.check_needed,
    }


def snapshot(package: ServicePackage, positions: list[ServicePackagePosition]) -> dict[str, Any]:
    return {
        "name": package.name,
        "nameTranslations": package.name_translations,
        "isActive": package.is_active,
        "positions": [_position_snapshot(position) for position in positions],
    }


# --- writes -----------------------------------------------------------------------------------


def _write_positions(
    db: Session,
    *,
    package: ServicePackage,
    old: list[ServicePackagePosition],
    data: list[PackagePositionInput],
    actor_id: uuid.UUID | None,
    unit_in_force: Callable[[], tuple[LabourTimeUnit, Decimal | None]],
    labels: dict[uuid.UUID, str],
) -> list[ServicePackagePosition]:
    """Replaces the package's positions. A labour position sent with its `id` and
    the time it holds keeps the unit it was typed in (ruling 183)."""

    kept = {position.id: position for position in old}
    for position in old:
        db.delete(position)
    db.flush()
    now = utcnow()
    written: list[ServicePackagePosition] = []
    for index, item in enumerate(data, start=1):
        position_text = _check_position(index, item)
        position = ServicePackagePosition(
            tenant_id=package.tenant_id,
            package_id=package.id,
            sort_index=index,
            kind=item.kind,
            text=position_text,
            check_needed=bool(item.check_needed) if item.kind is PackagePositionKind.LABOUR else False,
            created_by=actor_id,
            updated_by=actor_id,
        )
        if item.kind is PackagePositionKind.LABOUR:
            assert item.labour_category_id is not None and item.time is not None
            position.labour_category_id = item.labour_category_id
            position.labour_category_label = labels[item.labour_category_id]
            position.labour_category_label_refreshed_at = now
            previous = kept.get(item.id) if item.id is not None else None
            same_time = (
                previous is not None
                and previous.kind is PackagePositionKind.LABOUR
                and previous.time_quantity == item.time
                and (item.time_unit is None or item.time_unit is previous.time_unit)
            )
            if previous is not None and same_time:
                position.time_unit = previous.time_unit
                position.work_units_per_hour = previous.work_units_per_hour
            else:
                unit, per_hour = unit_in_force()
                if item.time_unit is not None and item.time_unit is not unit:
                    raise ConflictError(
                        f"Labour time is entered in {unit.value.replace('_', ' ')} here now — "
                        "check the time and enter it again.",
                        details={"reason": "service_package_time_unit_changed", "timeUnit": unit.value,
                                 "position": index},
                    )
                position.time_unit, position.work_units_per_hour = unit, per_hour
            position.time_quantity = item.time
        db.add(position)
        written.append(position)
    db.flush()
    return written


def _unit_in_force(db: Session, dealership_id: uuid.UUID) -> Callable[[], tuple[LabourTimeUnit, Decimal | None]]:
    """O18/O19 of the Betrieb itself (a package is Betrieb-wide), read once when first needed."""

    cache: list[tuple[LabourTimeUnit, Decimal | None]] = []

    def read() -> tuple[LabourTimeUnit, Decimal | None]:
        if not cache:
            unit = labour_time_unit(db, dealership_id=dealership_id, location_id=None)
            if unit.unit == LabourTimeUnit.WORK_UNITS.value:
                cache.append((LabourTimeUnit.WORK_UNITS, unit.work_units_per_hour))
            else:
                cache.append((LabourTimeUnit.HOURS, None))
        return cache[0]

    return read


def _prepare(db: Session, dealership_id: uuid.UUID, data: PackageInput) -> tuple[str, dict[str, str] | None,
                                                                                    dict[uuid.UUID, str]]:
    name = _clean_name(data.name)
    translations = _clean_translations(data.name_translations)
    for index, item in enumerate(data.positions, start=1):
        _check_position(index, item)
    category_ids = {item.labour_category_id for item in data.positions if item.labour_category_id is not None}
    return name, translations, _category_labels(db, dealership_id, category_ids)


def create_package(
    db: Session, *, dealership_id: uuid.UUID, actor_id: uuid.UUID | None, data: PackageInput, commit: bool = True
) -> tuple[ServicePackage, list[ServicePackagePosition]]:
    name, translations, labels = _prepare(db, dealership_id, data)
    lock_catalogue(db, dealership_id)
    if _name_taken(db, dealership_id, name, other_than=None):
        raise ConflictError(
            "The service catalogue already has a package of this name.",
            details={"reason": "service_package_name_taken"},
        )
    package = ServicePackage(
        tenant_id=dealership_id,
        name=name,
        name_translations=translations,
        is_active=data.is_active,
        created_by=actor_id,
        updated_by=actor_id,
    )
    db.add(package)
    db.flush()
    positions = _write_positions(
        db, package=package, old=[], data=data.positions, actor_id=actor_id,
        unit_in_force=_unit_in_force(db, dealership_id), labels=labels,
    )
    record_audit_event(
        db, entity_type=ENTITY_TYPE, entity_id=package.id, tenant_id=dealership_id, action="create",
        actor_id=actor_id, after=snapshot(package, positions),
    )
    if commit:
        db.commit()
    return package, positions


def change_package(
    db: Session,
    *,
    dealership_id: uuid.UUID,
    package_id: uuid.UUID,
    expected_version: int | None,
    actor_id: uuid.UUID | None,
    data: PackageInput,
    commit: bool = True,
) -> tuple[ServicePackage, list[ServicePackagePosition]]:
    """Replaces the package's content (the aggregate, If-Match). A write that
    changes nothing writes nothing: no version, no audit row. `expected_version`
    None is the configuration copy's own write, which holds the row lock."""

    name, translations, labels = _prepare(db, dealership_id, data)
    lock_catalogue(db, dealership_id)
    package = get_package_or_404(db, dealership_id=dealership_id, package_id=package_id, lock=True)
    if expected_version is not None:
        check_version(package.version, expected_version, entity_name="Service package")
    if _name_taken(db, dealership_id, name, other_than=package.id):
        raise ConflictError(
            "The service catalogue already has a package of this name.",
            details={"reason": "service_package_name_taken"},
        )
    old = positions_of(db, [package.id])[package.id]
    before = snapshot(package, old)
    nested = db.begin_nested()
    package.name, package.name_translations, package.is_active = name, translations, data.is_active
    positions = _write_positions(
        db, package=package, old=old, data=data.positions, actor_id=actor_id,
        unit_in_force=_unit_in_force(db, dealership_id), labels=labels,
    )
    after = snapshot(package, positions)
    if after == before:
        nested.rollback()
        db.expire_all()
        package = get_package_or_404(db, dealership_id=dealership_id, package_id=package_id)
        if commit:
            db.commit()
        return package, positions_of(db, [package.id])[package.id]
    nested.commit()
    package.version += 1
    package.updated_by = actor_id
    package.updated_at = utcnow()
    action = "update"
    if before["isActive"] != after["isActive"] and {**before, "isActive": None} == {**after, "isActive": None}:
        action = "activate" if data.is_active else "deactivate"
    record_audit_event(
        db, entity_type=ENTITY_TYPE, entity_id=package.id, tenant_id=dealership_id, action=action,
        actor_id=actor_id, before=before, after=after,
    )
    if commit:
        db.commit()
    return package, positions


# --- «from the catalogue» on a job (check 44) --------------------------------------------------


def copy_package_onto_job(
    db: Session,
    *,
    order: ServiceOrder,
    job: ServiceOrderJob,
    package_id: uuid.UUID,
    actor_id: uuid.UUID,
    confirmed: bool = False,
) -> list[ServiceOrderPosition]:
    """Copies an active package's positions into the job, after its own
    (section 3.10). From then on they are positions like typed ones: nothing
    names the package (check 45). Refused on a stopped job; an accepted job
    follows the customer's answer like a typed position (KAN-811). Commits."""

    refuse_frozen(order)
    answers.refuse_stopped(job)
    answers.refuse_unconfirmed_change_while_clocked(db, order=order, job=job, confirmed=confirmed)
    package = get_package_or_404(db, dealership_id=order.tenant_id, package_id=package_id)
    if not package.is_active:
        raise ConflictError(
            f"The service package «{package.name}» is inactive and is no longer offered.",
            details={"reason": "service_package_inactive"},
        )
    items = positions_of(db, [package.id])[package.id]
    categories = {item.labour_category_id for item in items if item.labour_category_id is not None}
    labels = labour_category_labels(db, dealership_id=order.tenant_id, category_ids=sorted(categories))
    vat_rate = get_dealership_or_404(db, order.tenant_id).vat_rate
    if categories and vat_rate is None:
        raise ConflictError(
            "No VAT rate is set for this business yet, so no price can be entered.",
            details={"reason": "service_order_vat_rate_not_set"},
        )
    proposals = {}
    for category_id in sorted(categories):
        label = labels.get(category_id)
        if label is None:
            raise ConflictError(
                "A labour category of this package no longer exists here.",
                details={"reason": "service_order_labour_category_unknown"},
            )
        proposal = propose_rate(db, order=order, category_id=category_id)
        if not proposal.found or proposal.amount is None:
            raise ConflictError(
                f"No labour rate applies to the category «{label.name}» at this location on the order's day, "
                "so the package cannot be put onto the job.",
                details={"reason": "service_package_labour_rate_missing", "labourCategoryId": str(category_id)},
            )
        proposals[category_id] = proposal

    last = db.scalar(select(func.max(ServiceOrderPosition.sort_index)).where(ServiceOrderPosition.job_id == job.id))
    now = utcnow()
    copied: list[ServiceOrderPosition] = []
    for offset, item in enumerate(items, start=1):
        position = ServiceOrderPosition(
            tenant_id=order.tenant_id,
            service_order_id=order.id,
            job_id=job.id,
            sort_index=(last or 0) + offset,
            kind=PositionKind.TEXT if item.kind is PackagePositionKind.TEXT else PositionKind.LABOUR_TIME,
            text=item.text,
            check_needed=item.check_needed,
            created_by=actor_id,
            updated_by=actor_id,
        )
        if item.kind is PackagePositionKind.LABOUR:
            assert item.labour_category_id is not None
            proposal = proposals[item.labour_category_id]
            position.labour_category_id = item.labour_category_id
            position.labour_category_label = labels[item.labour_category_id].name[:CATEGORY_LABEL_LENGTH]
            position.labour_category_label_refreshed_at = now
            # The time as the package holds it, in the unit it was typed in (ruling 183):
            # the amount is hours × the hourly rate, whatever the unit.
            position.time_quantity = item.time_quantity
            position.time_unit = item.time_unit
            position.work_units_per_hour = item.work_units_per_hour
            position.unit_price, position.price_entered = proposal.amount, proposal.entered
            position.rate_source, position.labour_rate_id = proposal.source, proposal.rate_id
            position.rate_valid_from = proposal.valid_from
            position.vat_rate = vat_rate
            position.priced_at, position.priced_by = now, actor_id
        db.add(position)
        copied.append(position)
    db.flush()
    # The history says which package the positions came from; the positions do not (check 45).
    # One request, one change of the order: its version moves once; each position gets its row.
    for index, position in enumerate(copied):
        after = {**position_snapshot(position), "servicePackageName": package.name}
        if index == 0:
            record_order_change(db, order, action="service_order_position_added", actor_id=actor_id, after=after)
        else:
            record_audit_event(
                db, entity_type=ORDER_ENTITY_TYPE, entity_id=order.id, tenant_id=order.tenant_id,
                action="service_order_position_added", actor_id=actor_id, after=after,
            )
    answers.follow_amount(db, order=order, job=job, actor_id=actor_id)
    db.commit()
    return copied


# --- the nightly label refresh (rule 2, rule 10) ----------------------------------------------


@dataclass
class PackageLabelReport:
    changed: list[tuple[uuid.UUID, str | None, str]] = field(default_factory=list)
    unresolved: list[uuid.UUID] = field(default_factory=list)
    restamped: int = 0


_BATCH = 500


def refresh_package_labels(db: Session, *, commit: Callable[[], None] | None = None) -> PackageLabelReport:
    """Re-reads every package position's labour-category name through
    `platform.public`, in id order, one commit per batch read FOR NO KEY UPDATE
    SKIP LOCKED. A copy refreshed on purpose: no package version moves. A
    category that names nothing is left alone (reconciliation reports it)."""

    finish = commit or db.commit
    report = PackageLabelReport()
    last_id: uuid.UUID | None = None
    while True:
        stmt = (
            select(ServicePackagePosition)
            .where(ServicePackagePosition.labour_category_id.is_not(None))
            .order_by(ServicePackagePosition.id)
            .limit(_BATCH)
            .with_for_update(key_share=True, skip_locked=True)
        )
        if last_id is not None:
            stmt = stmt.where(ServicePackagePosition.id > last_id)
        batch = list(db.scalars(stmt))
        if not batch:
            return report
        last_id = batch[-1].id
        now = utcnow()
        by_tenant: dict[uuid.UUID, set[uuid.UUID]] = {}
        for position in batch:
            assert position.labour_category_id is not None
            by_tenant.setdefault(position.tenant_id, set()).add(position.labour_category_id)
        labels = {
            tenant_id: labour_category_labels(db, dealership_id=tenant_id, category_ids=sorted(ids))
            for tenant_id, ids in by_tenant.items()
        }
        restamp: list[uuid.UUID] = []
        for position in batch:
            assert position.labour_category_id is not None
            found = labels[position.tenant_id].get(position.labour_category_id)
            if found is None:
                report.unresolved.append(position.id)
                continue
            current = found.name[:CATEGORY_LABEL_LENGTH]
            if current == position.labour_category_label:
                restamp.append(position.id)
                continue
            report.changed.append((position.id, position.labour_category_label, current))
            position.labour_category_label = current
            position.labour_category_label_refreshed_at = now
        db.flush()
        if restamp:
            db.execute(
                update(ServicePackagePosition)
                .where(ServicePackagePosition.id.in_(restamp))
                .values(labour_category_label_refreshed_at=now, updated_at=ServicePackagePosition.updated_at)
                .execution_options(synchronize_session=False)
            )
            report.restamped += len(restamp)
        finish()


def oldest_package_label_age_seconds(db: Session) -> float | None:
    """Age of the stalest labour-category label in the catalogue — the worker's
    `dms.label.age_seconds{label="aftersales.package"}` (rule 10). None when
    no package position names a category."""

    stamp = db.scalar(
        select(func.min(ServicePackagePosition.labour_category_label_refreshed_at)).where(
            ServicePackagePosition.labour_category_label_refreshed_at.is_not(None)
        )
    )
    if stamp is None:
        return None
    if stamp.tzinfo is None:
        stamp = stamp.replace(tzinfo=dt.UTC)
    return (utcnow() - stamp).total_seconds()


# --- «copy configuration from a sister Betrieb» (criterion 7) ---------------------------------


class ServiceCatalogueCopier:
    """The catalogue's part of KAN-220's copy (`app.core.configuration_copy`),
    registered at the composition root; platform runs it before its own
    subsets, and it commits — the dry run: rolls back — its own session.

    Every **active** package of the source (an inactive one is offered nowhere)
    is written into the target through this module's own write path — a
    package of the same name (case ignored) is overwritten, else created; a
    package the target holds that the source lacks is kept (absent = kept, as
    for texts). The copy is independent: nothing names the source.

    - A labour category is matched in the target by its name, case ignored,
      among the target's active categories (the categories themselves are
      platform's and not copied here, KAN-220's open subset); a package one of
      whose categories the target lacks is skipped whole
      (`labour_category_missing`) — a package lands whole or not at all.
    - A package's times keep their unit (ruling 183); a package whose unit or
      work units per hour differ from what the target has in force is skipped
      (`time_unit_differs`) rather than converted.
    - A package the target holds under the name and has set inactive is left
      as the manager left it (`package_inactive_here`): a copy never undoes a
      deactivation, as the contact-group copy never switches a group off.
    """

    subset: str = "service_catalogue"
    module: str | None = "aftersales"

    def copy(
        self,
        session_factory: Callable[[], Session],
        *,
        source_dealership_id: uuid.UUID,
        target_dealership_id: uuid.UUID,
        actor_id: uuid.UUID | None,
        dry_run: bool,
    ) -> list[CopiedItem]:
        """On a session of its own (rule 12): committed, or — the dry run —
        rolled back after the very same writes, so the preview is the commit."""

        db = session_factory()
        try:
            items = self._copy(
                db, source_dealership_id=source_dealership_id, target_dealership_id=target_dealership_id,
                actor_id=actor_id,
            )
            if dry_run:
                db.rollback()
            else:
                db.commit()
            return items
        except BaseException:
            db.rollback()
            raise
        finally:
            db.close()

    def _copy(
        self,
        db: Session,
        *,
        source_dealership_id: uuid.UUID,
        target_dealership_id: uuid.UUID,
        actor_id: uuid.UUID | None,
    ) -> list[CopiedItem]:
        lock_catalogue(db, target_dealership_id)
        packages = list_packages(db, dealership_id=source_dealership_id, active_only=True)
        positions = positions_of(db, [package.id for package in packages])
        source_names = {
            label.id: label.name for label in labour_categories_of_dealership(db, dealership_id=source_dealership_id)
        }
        target_categories = {
            label.name.lower(): label.id
            for label in labour_categories_of_dealership(db, dealership_id=target_dealership_id)
            if label.is_active
        }
        unit, per_hour = _unit_in_force(db, target_dealership_id)()
        existing = {
            package.name.lower(): package for package in list_packages(db, dealership_id=target_dealership_id)
        }
        items: list[CopiedItem] = []
        for package in packages:
            rows = positions[package.id]
            skip = None
            inputs: list[PackagePositionInput] = []
            for row in rows:
                if row.kind is PackagePositionKind.TEXT:
                    inputs.append(PackagePositionInput(kind=row.kind, text=row.text))
                    continue
                assert row.labour_category_id is not None
                name = source_names.get(row.labour_category_id, row.labour_category_label or "")
                target_category = target_categories.get(name.lower())
                if target_category is None:
                    skip = "labour_category_missing"
                    break
                if row.time_unit is not unit or row.work_units_per_hour != per_hour:
                    skip = "time_unit_differs"
                    break
                inputs.append(
                    PackagePositionInput(
                        kind=row.kind,
                        text=row.text,
                        labour_category_id=target_category,
                        time=row.time_quantity,
                        time_unit=row.time_unit,
                        check_needed=row.check_needed,
                    )
                )
            if skip is not None:
                items.append(CopiedItem(key=package.name, outcome=seam.SKIPPED, label=package.name, reason=skip))
                continue
            data = PackageInput(
                name=package.name,
                name_translations=dict(package.name_translations) if package.name_translations else None,
                is_active=True,
                positions=inputs,
            )
            mine = existing.get(package.name.lower())
            if mine is not None and not mine.is_active:
                items.append(CopiedItem(key=package.name, outcome=seam.SKIPPED, label=package.name,
                                        reason="package_inactive_here"))
                continue
            if mine is None:
                create_package(db, dealership_id=target_dealership_id, actor_id=actor_id, data=data, commit=False)
                items.append(CopiedItem(key=package.name, outcome=seam.CREATED, label=package.name))
                continue
            before = mine.version
            changed, _ = change_package(
                db,
                dealership_id=target_dealership_id,
                package_id=mine.id,
                expected_version=None,
                actor_id=actor_id,
                data=data,
                commit=False,
            )
            outcome = seam.OVERWRITTEN if changed.version != before else seam.UNCHANGED
            items.append(CopiedItem(key=package.name, outcome=outcome, label=package.name))
        return items
