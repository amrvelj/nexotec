"""A car in stock for a test that needs one (KAN-554).

Since KAN-554 (ADR-116) a create with a VIN lands in pipeline: only purchase
booking puts a car on stock. A test whose subject is not the purchase still
needs a car in stock, so it creates the item and promotes it (FR-V-04) — the
car is in stock under its VIN, with its vehicle-mdm record, and no purchase
booked, exactly what a create with a VIN gave before."""

import uuid

from sqlalchemy.orm import Session

from app.inventory.models.stock_item import StockItem
from app.inventory.schemas.stock_item import StockItemCreate
from app.inventory.services.pipeline import promote_to_vehicle_mdm
from app.inventory.services.stock_item import create_stock_item


def create_in_stock_item(
    db: Session, *, tenant_id: uuid.UUID, data: StockItemCreate, actor_id: uuid.UUID | None = None
) -> StockItem:
    """Create the item with its VIN, then promote it into stock."""

    assert data.vin is not None, "a car in stock has a VIN"
    item = create_stock_item(db, tenant_id=tenant_id, data=data, actor_id=actor_id)
    return promote_to_vehicle_mdm(db, item=item, vin=item.vin)


def promote_created_item(db: Session, *, stock_item_id: uuid.UUID | str) -> StockItem:
    """For a test that created the item over HTTP with a VIN: promote it into
    stock (FR-V-04) under the VIN it carries."""

    item = db.get(StockItem, uuid.UUID(str(stock_item_id)))
    assert item is not None and item.vin is not None
    item = promote_to_vehicle_mdm(db, item=item, vin=item.vin)
    return item


def supplier_contact(
    db: Session,
    *,
    tenant_id: uuid.UUID,
    name: str = "Hans Muster",
    business: bool = False,
    vat_registered: bool = False,
    vat_status_confirmed: bool = False,
    roles: tuple[str, ...] = ("supplier",),
) -> uuid.UUID:
    """KAN-465: a purchase booking names a contact with the supplier role, in
    the dealership's group. A contact for a test whose subject is not the
    supplier: by default a private individual (VAT prefill: not registered)."""

    from app.contact.schemas.contact import ContactCreate, ContactEmailCreate
    from app.contact.services import contact as contact_service
    from app.platform.public import get_dealership_or_404

    group_id = get_dealership_or_404(db, tenant_id).dealer_group_id
    names: dict[str, str] = (
        {"company_name": name}
        if business
        else {"first_name": name.split(" ", 1)[0], "last_name": (name.split(" ", 1)[1:] or ["Muster"])[0]}
    )
    contact = contact_service.create_contact(
        db,
        group_id=group_id,
        data=ContactCreate(
            contact_type="business" if business else "individual",
            language="de",
            roles=list(roles),
            vat_registered=vat_registered,
            vat_status_confirmed=vat_status_confirmed,
            emails=[
                ContactEmailCreate(
                    email_type="work" if business else "personal",
                    email_address=f"supplier-{uuid.uuid4().hex[:8]}@example.ch",
                    is_primary=True,
                )
            ],
            **names,
        ),
        actor_id=uuid.uuid4(),
        acting_dealership_id=tenant_id,
        dealership_id=tenant_id,
    )
    return contact.id


def supplier_for_item(db: Session, item_id: uuid.UUID | str, **kwargs) -> str:
    """`supplier_contact` in the Betrieb of the stock item `item_id`, as the
    `supplierContactId` of a purchase request body."""

    item = db.get(StockItem, uuid.UUID(str(item_id)))
    assert item is not None
    return str(supplier_contact(db, tenant_id=item.tenant_id, **kwargs))


def supplier_over_api(client, headers: dict[str, str], *, name: str = "Hans Muster") -> str:
    """KAN-465: a supplier contact created as the purchasing user creates one
    (POST /v1/contacts with the supplier role; inventory may give it), in
    the token's group — for a test that books over HTTP."""

    first, _, last = name.partition(" ")
    last = last or "Muster"
    response = client.post(
        "/v1/contacts",
        json={
            "contactType": "individual", "language": "de", "firstName": first, "lastName": last,
            "roles": ["supplier"],
            "emails": [{"emailType": "personal", "emailAddress": f"supplier-{uuid.uuid4().hex[:8]}@example.ch", "isPrimary": True}],
        },
        headers={key: value for key, value in headers.items() if key.lower() == "authorization"},
    )
    assert response.status_code == 201, response.text
    return response.json()["id"]
