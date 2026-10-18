"""Dealership column corrections: public UID, VAT number, Swiss address shape (KAN-197, D-A-22, D-A-25)

D-A-22 (2026-10-07): the Swiss UID is public (Zefix), so `tax_id` — encrypted
at rest and write-only — blocked indexing, checksum validation and the
duplicate-tenant check at onboarding (D-A-06). It becomes a plain, indexed
`uid` (CHE-123.456.789, mod-11 check digit) plus `vat_number` (CHE-… MWST)
and `vat_liable`. The two US artefacts `dealer_license_number` and
`license_state` are dropped; `dba_name` becomes `trade_name`; the postal
code widens to 12 like `location`'s. D-A-25: one registered-seat address plus
nullable correspondence and billing overrides.

Data step (skipped under `--sql`, KAN-174): every `tax_id` is decrypted with
the Fernet key the application already requires (Settings.tax_id_encryption_key
— alembic/env.py loads Settings, so a missing key fails before any DDL),
normalised and validated with a frozen copy of the UID rule (the live one is
app.core.validators.validate_swiss_uid; tests/test_kan197_dealership_migration.py
checks the two agree). A valid, first-seen UID is written to `uid`. A value
that does not decrypt, is not a UID, fails its check digit or repeats another
row's UID is printed as a `KAN-197 REPORT:` line and the row keeps `uid` NULL
— never garbage in an indexed column. Rows are visited oldest first, so the
first-seen holder of a duplicated UID is deterministic. If not one encrypted
value decrypts, the key is wrong, not the data, and the upgrade aborts before
anything is dropped. Counts are printed and verified by query; the UID is
public, so the report may name it.

Downgrade reverses the DDL. `uid` is re-encrypted into `tax_id`; a row with
`uid` NULL gets an encrypted empty string (the old column was NOT NULL).
`dealer_license_number` and `license_state` cannot be restored: they come
back as '' and '??'. A postal code longer than four characters is truncated.

Revision ID: 4f8a2c7e1d93
Revises: 61f21e3f6b90
Create Date: 2026-10-17 08:40:00.000000

"""

import re
from collections.abc import Callable, Iterable, Sequence

import sqlalchemy as sa
from cryptography.fernet import Fernet
from sqlalchemy.dialects import postgresql

from alembic import context, op

# revision identifiers, used by Alembic.
revision: str = "4f8a2c7e1d93"
down_revision: str | Sequence[str] | None = "61f21e3f6b90"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# Frozen copy of the UID rule (app.core.validators): a migration must keep
# meaning what it meant when it ran.
_UID_RE = re.compile(r"^CHE-[0-9]{3}\.[0-9]{3}\.[0-9]{3}$")
_UID_WEIGHTS = (5, 4, 3, 2, 7, 6, 5, 4)

# (suffix, length) of one address block — the six columns of the registered
# seat, repeated for the two D-A-25 overrides.
_ADDRESS_COLUMNS: tuple[tuple[str, int], ...] = (
    ("street", 200),
    ("house_number", 20),
    ("postal_code", 12),
    ("locality", 100),
    ("canton", 2),
    ("country", 2),
)
_OVERRIDE_PREFIXES = ("correspondence_address", "billing_address")

_dealership = sa.table(
    "dealership",
    sa.column("id", postgresql.UUID(as_uuid=True)),
    sa.column("legal_name", sa.String()),
    sa.column("tax_id", sa.Text()),
    sa.column("uid", sa.String()),
    sa.column("created_at", sa.DateTime(timezone=True)),
)


def _uid_check_digit(first_eight: str) -> int | None:
    total = sum(int(d) * w for d, w in zip(first_eight, _UID_WEIGHTS, strict=True))
    check = 11 - (total % 11)
    if check == 10:
        return None
    return 0 if check == 11 else check


def uid_from_tax_id(plaintext: str) -> tuple[str | None, str | None]:
    """The normalised UID and None, or None and the reason it is not one."""

    normalised = plaintext.strip().upper()
    if not _UID_RE.match(normalised):
        return None, "is not in the format CHE-123.456.789"
    digits = normalised[4:].replace(".", "")
    expected = _uid_check_digit(digits[:8])
    if expected is None or expected != int(digits[8]):
        return None, "fails the UID check digit"
    return normalised, None


def convert_rows(
    rows: Iterable[tuple[object, str, str | None]], decrypt: Callable[[str], str]
) -> tuple[list[tuple[object, str]], list[str]]:
    """Pure: (id, legal_name, ciphertext) rows -> the (id, uid) updates to
    write and the report lines for every row left at NULL. The first row
    with a UID keeps it; a repeat is reported, never silently deduplicated."""

    seen: dict[str, object] = {}
    updates: list[tuple[object, str]] = []
    report: list[str] = []
    undecryptable = 0
    encrypted = 0
    for row_id, legal_name, ciphertext in rows:
        prefix = f"KAN-197 REPORT: dealership {row_id} ({legal_name}):"
        if ciphertext is None:
            report.append(f"{prefix} tax_id is NULL — uid left NULL")
            continue
        encrypted += 1
        try:
            plaintext = decrypt(ciphertext)
        except Exception:  # noqa: BLE001 — any failure to decrypt is the same outcome
            undecryptable += 1
            report.append(f"{prefix} tax_id does not decrypt with the configured key — uid left NULL")
            continue
        uid, reason = uid_from_tax_id(plaintext)
        if uid is None:
            report.append(f"{prefix} tax_id {plaintext!r} {reason} — uid left NULL")
            continue
        if uid in seen:
            report.append(f"{prefix} tax_id {plaintext!r} repeats the UID of dealership {seen[uid]} — uid left NULL")
            continue
        seen[uid] = row_id
        updates.append((row_id, uid))
    if encrypted and undecryptable == encrypted:
        # Not one row decrypts: that is the wrong key, not bad data. Dropping
        # tax_id now would lose every UID, so the upgrade stops here instead.
        raise RuntimeError(
            f"KAN-197: none of the {encrypted} encrypted tax_id value(s) decrypts with the configured "
            "DMS_TAX_ID_ENCRYPTION_KEY — refusing to continue (the key is wrong, not the data)"
        )
    return updates, report


def _fernet() -> Fernet:
    # The key the application itself decrypts with. alembic/env.py has already
    # loaded Settings (its database URL comes from there), and Settings has no
    # default for the key: without it this run never reaches the first DDL.
    from app.core.config import get_settings

    return Fernet(get_settings().tax_id_encryption_key.encode("ascii"))


def _convert_tax_ids() -> None:
    if context.is_offline_mode():
        op.execute(
            "-- KAN-197 tax_id -> uid conversion skipped: it reads rows and needs a live database. "
            "WARNING: the DROP COLUMN tax_id below would then discard every UID — run this migration online."
        )
        return
    conn = op.get_bind()
    fernet = _fernet()
    # Ordered, so "first-seen" for a duplicated UID is the oldest row, not
    # whatever physical order the table happens to have.
    rows = conn.execute(
        sa.select(_dealership.c.id, _dealership.c.legal_name, _dealership.c.tax_id).order_by(
            _dealership.c.created_at, _dealership.c.id
        )
    ).all()
    updates, report = convert_rows(
        [(row_id, legal_name, ciphertext) for row_id, legal_name, ciphertext in rows],
        lambda ciphertext: fernet.decrypt(ciphertext.encode("ascii")).decode("utf-8"),
    )
    for row_id, uid in updates:
        conn.execute(sa.update(_dealership).where(_dealership.c.id == row_id).values(uid=uid))
    for line in report:
        print(line)
    set_count = conn.execute(
        sa.select(sa.func.count()).select_from(_dealership).where(_dealership.c.uid.is_not(None))
    ).scalar()
    print(f"KAN-197: {len(rows)} dealership row(s): {set_count} uid set, {len(rows) - (set_count or 0)} left NULL")
    if set_count != len(updates):
        raise RuntimeError(f"KAN-197: wrote {len(updates)} uid value(s) but {set_count} are set — aborting")


def _reencrypt_uids() -> None:
    if context.is_offline_mode():
        op.execute("-- KAN-197 downgrade uid -> tax_id re-encryption skipped: it reads rows and needs a live database")
        return
    conn = op.get_bind()
    fernet = _fernet()
    rows = conn.execute(sa.select(_dealership.c.id, _dealership.c.uid)).all()
    for row_id, uid in rows:
        ciphertext = fernet.encrypt((uid or "").encode("utf-8")).decode("ascii")
        conn.execute(sa.update(_dealership).where(_dealership.c.id == row_id).values(tax_id=ciphertext))
    print(f"KAN-197 downgrade: {len(rows)} dealership row(s) re-encrypted into tax_id")


def upgrade() -> None:
    op.add_column("dealership", sa.Column("uid", sa.String(length=15), nullable=True))
    op.add_column("dealership", sa.Column("vat_number", sa.String(length=20), nullable=True))
    op.add_column(
        "dealership", sa.Column("vat_liable", sa.Boolean(), nullable=False, server_default=sa.false())
    )
    # The default only backfilled the existing rows; the model declares a Python
    # default, not a server one (the project's precedent, e.g. credit_block).
    op.alter_column("dealership", "vat_liable", server_default=None)
    for prefix in _OVERRIDE_PREFIXES:
        for suffix, length in _ADDRESS_COLUMNS:
            op.add_column("dealership", sa.Column(f"{prefix}_{suffix}", sa.String(length=length), nullable=True))
    op.alter_column(
        "dealership",
        "dba_name",
        new_column_name="trade_name",
        existing_type=sa.String(length=200),
        existing_nullable=True,
    )
    op.alter_column(
        "dealership",
        "address_postal_code",
        existing_type=sa.String(length=4),
        type_=sa.String(length=12),
        existing_nullable=False,
    )
    _convert_tax_ids()
    op.create_index("uq_dealership_uid", "dealership", ["uid"], unique=True)
    op.drop_column("dealership", "tax_id")
    op.drop_column("dealership", "dealer_license_number")
    op.drop_column("dealership", "license_state")


def downgrade() -> None:
    op.add_column("dealership", sa.Column("tax_id", sa.Text(), nullable=True))
    op.add_column(
        "dealership",
        sa.Column("dealer_license_number", sa.String(length=64), nullable=False, server_default=""),
    )
    op.add_column(
        "dealership", sa.Column("license_state", sa.String(length=2), nullable=False, server_default="??")
    )
    _reencrypt_uids()
    op.alter_column("dealership", "tax_id", existing_type=sa.Text(), nullable=False)
    # 61f21e3f6b90's shape has no server defaults on these two columns.
    op.alter_column("dealership", "dealer_license_number", server_default=None)
    op.alter_column("dealership", "license_state", server_default=None)
    op.drop_index("uq_dealership_uid", table_name="dealership")
    op.drop_column("dealership", "uid")
    op.drop_column("dealership", "vat_number")
    op.drop_column("dealership", "vat_liable")
    for prefix in _OVERRIDE_PREFIXES:
        for suffix, _length in _ADDRESS_COLUMNS:
            op.drop_column("dealership", f"{prefix}_{suffix}")
    op.alter_column(
        "dealership",
        "trade_name",
        new_column_name="dba_name",
        existing_type=sa.String(length=200),
        existing_nullable=True,
    )
    op.alter_column(
        "dealership",
        "address_postal_code",
        existing_type=sa.String(length=12),
        type_=sa.String(length=4),
        existing_nullable=False,
        postgresql_using="left(address_postal_code, 4)",
    )
