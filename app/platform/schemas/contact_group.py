"""Contact groups per Betrieb (KAN-466)."""

from app.core.schemas import CamelModel


class ContactGroupRead(CamelModel):
    """One group of the platform list `contact_group`, as this Betrieb sees
    it. `active` is the platform's (staff deactivate a value on the Reference
    data screen); `enabled` is this Betrieb's switch, never true for an
    inactive value."""

    value_code: str
    label_de: str
    label_fr: str
    label_it: str
    label_en: str
    sort_order: int
    active: bool
    enabled: bool


class ContactGroupList(CamelModel):
    items: list[ContactGroupRead]


class ContactGroupSwitch(CamelModel):
    enabled: bool
