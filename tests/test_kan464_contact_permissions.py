"""KAN-464 PR-2 — who writes contacts and gives which role (Anto, Q3 of
2026-11-14, A), and one number for every contact (Q2, A).

- inventory (purchasing) writes contacts too and gives the supplier role;
- the customer role and its data stay with sales and aftersales (the matrix
  row `contact_customer_role`): giving or taking the role, a changed
  lifecycle value of the role's, a changed advisor, the credit block, and a
  merge of a contact that holds it — 403 `customer_role_not_permitted`, and
  nothing changes;
- do-not-contact is every contact's: inventory sets it and lifts it;
- a supplier-only contact takes its number from the same sequence as a
  customer.
"""

import uuid

from app.core.auth import AccessRole
from app.core.permissions import writable_capabilities
from tests.membership_support import bearer, create_dealership, token


def _as(dealership: str, role: AccessRole | None = None, *, manager: bool = False) -> dict[str, str]:
    return bearer(token(role, tenant_id=dealership, is_dealer_manager=manager))


def _create(client, headers, **fields):
    payload = {
        "firstName": "Anna",
        "lastName": "Muster",
        "language": "de",
        "emails": [{"emailType": "personal", "emailAddress": f"anna-{uuid.uuid4().hex[:8]}@example.ch"}],
        **fields,
    }
    return client.post("/v1/customers", json=payload, headers={**headers, "Idempotency-Key": str(uuid.uuid4())})


def _created(client, headers, **fields) -> dict:
    response = _create(client, headers, **fields)
    assert response.status_code == 201, response.text
    return response.json()


def _patch(client, headers, contact, **body):
    return client.patch(
        f"/v1/customers/{contact['id']}", json=body, headers={**headers, "If-Match": str(contact["version"])}
    )


def _get(client, headers, contact_id) -> dict:
    response = client.get(f"/v1/customers/{contact_id}", headers=headers)
    assert response.status_code == 200, response.text
    return response.json()


def _refused(response, field: str) -> None:
    assert response.status_code == 403, response.text
    assert response.json()["error"]["details"] == {"reason": "customer_role_not_permitted", "field": field}


def test_the_matrix_gives_inventory_contacts_and_keeps_the_customer_role_with_sales_and_aftersales():
    for role in (AccessRole.SALES, AccessRole.AFTERSALES):
        writable = writable_capabilities(roles=frozenset({role}), is_dealer_manager=False)
        assert {"customers", "contact_customer_role"} <= set(writable)
    inventory = writable_capabilities(roles=frozenset({AccessRole.INVENTORY}), is_dealer_manager=False)
    assert "customers" in inventory and "contact_customer_role" not in inventory
    for role in (AccessRole.PARTS, AccessRole.FINANCE, AccessRole.AUDITOR):
        assert "customers" not in writable_capabilities(roles=frozenset({role}), is_dealer_manager=False)
    assert "contact_customer_role" in writable_capabilities(roles=frozenset(), is_dealer_manager=True)


def test_inventory_creates_a_supplier_and_is_refused_a_customer(client):
    dealership = create_dealership(client)
    inventory = _as(dealership, AccessRole.INVENTORY)

    supplier = _created(client, inventory, roles=["supplier"])
    assert supplier["roles"] == ["supplier"]

    # Left out, `roles` is a customer — which inventory may not give.
    _refused(_create(client, inventory), "roles")
    _refused(_create(client, inventory, roles=["customer", "supplier"]), "roles")


def test_parts_writes_no_contact(client):
    dealership = create_dealership(client)
    response = _create(client, _as(dealership, AccessRole.PARTS), roles=["supplier"])
    assert response.status_code == 403, response.text


def test_sales_and_aftersales_give_both_roles(client):
    dealership = create_dealership(client)
    for role in (AccessRole.SALES, AccessRole.AFTERSALES):
        headers = _as(dealership, role)
        contact = _created(client, headers, roles=["supplier"])
        response = _patch(client, headers, contact, roles=["customer", "supplier"])
        assert response.status_code == 200, response.text
        response = _patch(client, headers, response.json(), roles=["customer"])
        assert response.status_code == 200, response.text
        assert response.json()["roles"] == ["customer"]


def test_inventory_gives_the_supplier_role_to_a_customer_and_takes_it_away(client):
    dealership = create_dealership(client)
    customer = _created(client, _as(dealership, AccessRole.SALES))
    inventory = _as(dealership, AccessRole.INVENTORY)

    response = _patch(client, inventory, customer, roles=["customer", "supplier"])
    assert response.status_code == 200, response.text
    response = _patch(client, inventory, response.json(), roles=["customer"])
    assert response.status_code == 200, response.text
    assert response.json()["roles"] == ["customer"]


def test_inventory_neither_gives_nor_takes_the_customer_role(client):
    dealership = create_dealership(client)
    sales = _as(dealership, AccessRole.SALES)
    inventory = _as(dealership, AccessRole.INVENTORY)

    supplier = _created(client, inventory, roles=["supplier"])
    _refused(_patch(client, inventory, supplier, roles=["customer", "supplier"]), "roles")
    assert _get(client, sales, supplier["id"])["roles"] == ["supplier"]

    both = _created(client, sales, roles=["customer", "supplier"])
    _refused(_patch(client, inventory, both, roles=["supplier"]), "roles")
    unchanged = _get(client, sales, both["id"])
    assert unchanged["roles"] == ["customer", "supplier"]
    assert unchanged["version"] == both["version"]


def test_inventory_edits_a_customers_contact_data_but_not_its_customer_fields(client):
    dealership = create_dealership(client)
    sales = _as(dealership, AccessRole.SALES)
    inventory = _as(dealership, AccessRole.INVENTORY)
    customer = _created(client, sales, lifecycleStatus="prospect")

    response = _patch(client, inventory, customer, notes="Verkauft uns seinen Golf.", lifecycleStatus="prospect")
    assert response.status_code == 200, response.text  # a customer field sent unchanged is no write
    customer = response.json()
    assert customer["notes"] == "Verkauft uns seinen Golf."

    _refused(_patch(client, inventory, customer, lifecycleStatus="active"), "lifecycleStatus")
    _refused(_patch(client, inventory, customer, advisorId=str(uuid.uuid4())), "advisorId")
    assert _get(client, sales, customer["id"])["lifecycleStatus"] == "prospect"


def test_do_not_contact_is_set_by_every_writer_and_lifted_on_a_customer_by_sales(client):
    dealership = create_dealership(client)
    sales = _as(dealership, AccessRole.SALES)
    inventory = _as(dealership, AccessRole.INVENTORY)
    customer = _created(client, sales, lifecycleStatus="prospect")

    response = _patch(client, inventory, customer, lifecycleStatus="do_not_contact")
    assert response.status_code == 200, response.text
    # Lifting it chooses a customer value: else a prospect would become an
    # active customer in two steps.
    _refused(_patch(client, inventory, response.json(), lifecycleStatus="active"), "lifecycleStatus")
    _refused(_patch(client, inventory, response.json(), lifecycleStatus="prospect"), "lifecycleStatus")
    lifted = _patch(client, sales, response.json(), lifecycleStatus="prospect")
    assert lifted.status_code == 200, lifted.text

    # A supplier-only contact has no customer value: every writer lifts it.
    supplier = _created(client, inventory, roles=["supplier"])
    response = _patch(client, inventory, supplier, lifecycleStatus="do_not_contact")
    assert response.status_code == 200, response.text
    response = _patch(client, inventory, response.json(), lifecycleStatus="active")
    assert response.status_code == 200, response.text


def test_the_credit_block_is_written_by_sales_and_aftersales_only(client):
    dealership = create_dealership(client)
    sales = _as(dealership, AccessRole.SALES)
    inventory = _as(dealership, AccessRole.INVENTORY)
    customer = _created(client, sales)

    def _block(headers, contact, blocked: bool):
        return client.post(
            f"/v1/customers/{contact['id']}/credit-block",
            json={"blocked": blocked, "reason": "Offene Rechnungen" if blocked else None},
            headers={**headers, "If-Match": str(contact["version"]), "Idempotency-Key": str(uuid.uuid4())},
        )

    _refused(_block(inventory, customer, True), "creditBlock")
    blocked = _block(sales, customer, True)
    assert blocked.status_code == 200, blocked.text
    _refused(_block(inventory, blocked.json(), False), "creditBlock")
    assert _get(client, sales, customer["id"])["creditBlock"] is True


def _merge(client, headers, duplicate, survivor):
    return client.post(
        f"/v1/customers/{duplicate['id']}/merge",
        json={"duplicateOfCustomerId": survivor["id"]},
        headers={**headers, "If-Match": str(duplicate["version"]), "Idempotency-Key": str(uuid.uuid4())},
    )


def test_inventory_merges_suppliers_but_not_a_customer(client):
    dealership = create_dealership(client)
    sales = _as(dealership, AccessRole.SALES)
    inventory = _as(dealership, AccessRole.INVENTORY)

    survivor = _created(client, inventory, roles=["supplier"])
    duplicate = _created(client, inventory, roles=["supplier"])
    assert _merge(client, inventory, duplicate, survivor).status_code == 200

    supplier = _created(client, inventory, roles=["supplier"])
    customer = _created(client, sales)
    # Either side holding the customer role refuses it; nothing is merged.
    _refused(_merge(client, inventory, supplier, customer), "merge")
    _refused(_merge(client, inventory, customer, supplier), "merge")
    # The refusal comes after the child rows moved; they are back on the duplicate.
    for contact in (supplier, customer):
        emails = client.get(f"/v1/customers/{contact['id']}/emails", headers=sales).json()["items"]
        assert len(emails) == 1, contact
    assert _get(client, sales, supplier["id"])["lifecycleStatus"] == "active"
    assert _get(client, sales, customer["id"])["lifecycleStatus"] != "merged"
    assert _merge(client, sales, supplier, customer).status_code == 200


def test_a_supplier_takes_its_number_from_the_customers_sequence(client):
    dealership = create_dealership(client)
    sales = _as(dealership, AccessRole.SALES)
    first = _created(client, sales)
    supplier = _created(client, _as(dealership, AccessRole.INVENTORY), roles=["supplier"])
    last = _created(client, sales)

    numbers = [first["customerNumber"], supplier["customerNumber"], last["customerNumber"]]
    assert all(number.startswith("K-") for number in numbers)
    assert [int(number.removeprefix("K-")) for number in numbers] == list(
        range(int(numbers[0].removeprefix("K-")), int(numbers[0].removeprefix("K-")) + 3)
    )


def test_every_call_in_app_passes_the_customer_role_cell():
    """The services default `may_write_customer_role` to trusted (tests and
    scripts); a call in `app/` that left it out would grant the customer
    role to anyone who writes contacts."""

    import ast
    import pathlib

    guarded = {"create_customer", "update_customer", "set_credit_block", "merge_customer"}
    root = pathlib.Path(__file__).resolve().parents[1] / "app"
    calls = []
    for path in root.rglob("*.py"):
        for node in ast.walk(ast.parse(path.read_text())):
            if isinstance(node, ast.Call):
                name = getattr(node.func, "attr", None) or getattr(node.func, "id", None)
                if name in guarded:
                    calls.append((path, node))
    assert calls, "the guarded services are called from app/"
    missing = [
        f"{path.relative_to(root.parent)}:{node.lineno}"
        for path, node in calls
        if "may_write_customer_role" not in {kw.arg for kw in node.keywords}
    ]
    assert missing == []
