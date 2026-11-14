"""What another context may know of a Betrieb's publishing profile
(KAN-225, FR-A-05): the items a channel lacks, as plain data — never the
profile row. Exported through `platform.public`."""

import uuid
from dataclasses import dataclass

from sqlalchemy.orm import Session

from app.platform.organisation.services.dealership import get_dealership_or_404
from app.platform.services import publishing_profile


@dataclass(frozen=True)
class PublishingMissingItem:
    """One thing the channel needs. `code` is stable (the SPA's
    `publishingValidation.<code>` renders it in four languages)."""

    code: str
    section: str
    location_id: uuid.UUID | None
    location_name: str | None


def publishing_profile_missing_items(
    db: Session, *, dealership_id: uuid.UUID, channel: str = "website"
) -> list[PublishingMissingItem]:
    dealership = get_dealership_or_404(db, dealership_id)
    return [
        PublishingMissingItem(code=m.code, section=m.section, location_id=m.location_id, location_name=m.location_name)
        for m in publishing_profile.missing_items(db, dealership, channel)
    ]


def publishing_profile_ready(db: Session, *, dealership_id: uuid.UUID, channel: str = "website") -> bool:
    return not publishing_profile_missing_items(db, dealership_id=dealership_id, channel=channel)
