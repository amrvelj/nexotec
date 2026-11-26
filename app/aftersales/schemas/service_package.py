"""The service catalogue's API shapes (KAN-818, S1 in Dealer Administration;
D13 «from the catalogue» on a job)."""

import datetime as dt
import uuid
from decimal import Decimal

from pydantic import Field

from app.aftersales.models.service_order import LabourTimeUnit
from app.aftersales.models.service_package import PackagePositionKind
from app.core.schemas import CamelModel
from app.core.validators import DecimalStr


class PackagePositionWrite(CamelModel):
    """One position as the manager typed it. `id` names a position the package
    holds: sent with the same time, it keeps the unit it was typed in."""

    id: uuid.UUID | None = None
    kind: PackagePositionKind
    text: str
    labour_category_id: uuid.UUID | None = None
    time: DecimalStr | None = None
    # The unit the time was typed in; refused when it is no longer the one in force.
    time_unit: LabourTimeUnit | None = None
    check_needed: bool = False


class ServicePackageWrite(CamelModel):
    """A new package, or the whole package on a change (PUT with If-Match)."""

    name: str
    name_translations: dict[str, str] | None = None
    is_active: bool = True
    positions: list[PackagePositionWrite] = Field(default_factory=list, max_length=100)


class PackageTimeRead(CamelModel):
    quantity: Decimal
    unit: LabourTimeUnit
    work_units_per_hour: Decimal | None


class PackagePositionRead(CamelModel):
    id: uuid.UUID
    sort_index: int
    kind: PackagePositionKind
    text: str
    labour_category_id: uuid.UUID | None
    labour_category_label: str | None
    time: PackageTimeRead | None
    check_needed: bool


class ServicePackageRead(CamelModel):
    id: uuid.UUID
    name: str
    # Ruling 176: a name per language given; the screen shows the user's, else `name`.
    name_translations: dict[str, str]
    is_active: bool
    positions: list[PackagePositionRead]
    version: int
    updated_at: dt.datetime


class ServicePackageList(CamelModel):
    items: list[ServicePackageRead]


class JobPackageCopy(CamelModel):
    """D13: put a package from the catalogue onto the job."""

    service_package_id: uuid.UUID
