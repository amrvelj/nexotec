"""KAN-464 PR-3: the contact context's tables, columns, indexes and constraints

Customers became contacts (ADR-210; Anto, 2026-10-26: one register with roles, the full
rename). Renames only, in the catalogue: no row is rewritten, and every name is listed
explicitly because Postgres renames no index or constraint with its table or column. The
applied history keeps its names, the chain its label `customer`.

Revision ID: f0127c9deaf6
Revises: 7d66324be83a
Create Date: 2026-11-24 12:00:00.000000

"""

from collections.abc import Sequence

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "f0127c9deaf6"
down_revision: str | Sequence[str] | None = "7d66324be83a"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# (old, new) — the tables
_TABLES = (
    ("customer", "contact"),
    ("customer_address", "contact_address"),
    ("customer_contact_group", "contact_group_assignment"),
    ("customer_email", "contact_email"),
    ("customer_external_id", "contact_external_id"),
    ("customer_number_sequence", "contact_number_sequence"),
    ("customer_phone", "contact_phone"),
    ("customer_role", "contact_role"),
    ("customer_stock_item_booking", "contact_stock_item_booking"),
    ("customer_tag", "contact_tag"),
)

# (table as it is named after the table renames, old column, new column)
_COLUMNS = (
    ("contact", "customer_number", "contact_number"),
    ("contact", "customer_since", "contact_since"),
    ("contact", "customer_type", "contact_type"),
    ("contact", "duplicate_of_customer_id", "duplicate_of_contact_id"),
    ("contact_address", "customer_id", "contact_id"),
    ("contact_group_assignment", "customer_id", "contact_id"),
    ("contact_email", "customer_id", "contact_id"),
    ("contact_external_id", "customer_id", "contact_id"),
    ("contact_phone", "customer_id", "contact_id"),
    ("contact_role", "customer_id", "contact_id"),
    ("contact_tag", "customer_id", "contact_id"),
    ("legal_basis", "customer_id", "contact_id"),
    ("vehicle_party", "customer_id", "contact_id"),
    ("vehicle_party", "merged_into_customer_id", "merged_into_contact_id"),
)

# (table, old, new) — primary keys, unique, check and foreign-key constraints;
# a renamed primary key or unique constraint renames its index with it
_CONSTRAINTS = (
    ("contact", "ck_customer_customer_type_enum", "ck_contact_contact_type_enum"),
    ("contact", "ck_customer_gender_enum", "ck_contact_gender_enum"),
    ("contact", "ck_customer_language_enum", "ck_contact_language_enum"),
    ("contact", "ck_customer_legal_form_enum", "ck_contact_legal_form_enum"),
    ("contact", "ck_customer_lifecycle_status_enum", "ck_contact_lifecycle_status_enum"),
    ("contact", "ck_customer_payment_terms_enum", "ck_contact_payment_terms_enum"),
    ("contact", "ck_customer_preferred_channel_enum", "ck_contact_preferred_channel_enum"),
    ("contact", "ck_customer_salutation_enum", "ck_contact_salutation_enum"),
    ("contact", "ck_customer_source_enum", "ck_contact_source_enum"),
    ("contact", "customer_pkey", "contact_pkey"),
    ("contact", "fk_customer_duplicate_of_customer_id_customer", "fk_contact_duplicate_of_contact_id_contact"),
    ("contact", "uq_customer_group_id_customer_number", "uq_contact_group_id_contact_number"),
    ("contact_address", "ck_customer_address_address_type_enum", "ck_contact_address_address_type_enum"),
    ("contact_address", "ck_customer_address_consent_scope_enum", "ck_contact_address_consent_scope_enum"),
    ("contact_address", "ck_customer_address_consent_source_enum", "ck_contact_address_consent_source_enum"),
    ("contact_address", "customer_address_customer_id_fkey", "contact_address_contact_id_fkey"),
    ("contact_address", "customer_address_pkey", "contact_address_pkey"),
    ("contact_group_assignment", "customer_contact_group_customer_id_fkey", "contact_group_assignment_contact_id_fkey"),
    ("contact_group_assignment", "customer_contact_group_pkey", "contact_group_assignment_pkey"),
    ("contact_group_assignment", "uq_customer_contact_group_customer_id_value_code", "uq_contact_group_assignment_contact_id_value_code"),
    ("contact_email", "ck_customer_email_consent_scope_enum", "ck_contact_email_consent_scope_enum"),
    ("contact_email", "ck_customer_email_consent_source_enum", "ck_contact_email_consent_source_enum"),
    ("contact_email", "ck_customer_email_email_type_enum", "ck_contact_email_email_type_enum"),
    ("contact_email", "customer_email_customer_id_fkey", "contact_email_contact_id_fkey"),
    ("contact_email", "customer_email_pkey", "contact_email_pkey"),
    ("contact_email", "uq_customer_email_customer_id_address", "uq_contact_email_contact_id_address"),
    ("contact_external_id", "customer_external_id_customer_id_fkey", "contact_external_id_contact_id_fkey"),
    ("contact_external_id", "customer_external_id_pkey", "contact_external_id_pkey"),
    ("contact_external_id", "uq_customer_external_id_customer_system", "uq_contact_external_id_contact_system"),
    ("contact_external_id", "uq_customer_external_id_group_system_external", "uq_contact_external_id_group_system_external"),
    ("contact_number_sequence", "customer_number_sequence_pkey", "contact_number_sequence_pkey"),
    ("contact_phone", "ck_customer_phone_consent_scope_enum", "ck_contact_phone_consent_scope_enum"),
    ("contact_phone", "ck_customer_phone_consent_source_enum", "ck_contact_phone_consent_source_enum"),
    ("contact_phone", "ck_customer_phone_phone_type_enum", "ck_contact_phone_phone_type_enum"),
    ("contact_phone", "customer_phone_customer_id_fkey", "contact_phone_contact_id_fkey"),
    ("contact_phone", "customer_phone_pkey", "contact_phone_pkey"),
    ("contact_phone", "uq_customer_phone_customer_id_e164", "uq_contact_phone_contact_id_e164"),
    ("contact_role", "ck_customer_role_role_enum", "ck_contact_role_role_enum"),
    ("contact_role", "customer_role_customer_id_fkey", "contact_role_contact_id_fkey"),
    ("contact_role", "customer_role_pkey", "contact_role_pkey"),
    ("contact_stock_item_booking", "ck_customer_stock_item_booking_kind_enum", "ck_contact_stock_item_booking_kind_enum"),
    ("contact_stock_item_booking", "customer_stock_item_booking_pkey", "contact_stock_item_booking_pkey"),
    ("contact_tag", "customer_tag_customer_id_fkey", "contact_tag_contact_id_fkey"),
    ("contact_tag", "customer_tag_pkey", "contact_tag_pkey"),
    ("contact_tag", "uq_customer_tag_customer_id_tag", "uq_contact_tag_contact_id_tag"),
)

# (old, new) — the plain and partial indexes
_INDEXES = (
    ("ix_customer_advisor_id", "ix_contact_advisor_id"),
    ("ix_customer_company_name", "ix_contact_company_name"),
    ("ix_customer_customer_number", "ix_contact_contact_number"),
    ("ix_customer_dealership_id", "ix_contact_dealership_id"),
    ("ix_customer_group_id", "ix_contact_group_id"),
    ("ix_customer_group_id_created_at", "ix_contact_group_id_created_at"),
    ("ix_customer_group_id_last_name", "ix_contact_group_id_last_name"),
    ("ix_customer_group_id_updated_at", "ix_contact_group_id_updated_at"),
    ("ix_customer_address_customer_id", "ix_contact_address_contact_id"),
    ("ix_customer_address_group_id", "ix_contact_address_group_id"),
    ("ix_customer_contact_group_customer_id", "ix_contact_group_assignment_contact_id"),
    ("ix_customer_contact_group_group_id", "ix_contact_group_assignment_group_id"),
    ("ix_customer_email_customer_id", "ix_contact_email_contact_id"),
    ("ix_customer_email_email_address", "ix_contact_email_email_address"),
    ("ix_customer_email_group_id", "ix_contact_email_group_id"),
    ("ix_customer_external_id_customer_id", "ix_contact_external_id_contact_id"),
    ("ix_customer_external_id_group_id", "ix_contact_external_id_group_id"),
    ("ix_customer_phone_customer_id", "ix_contact_phone_contact_id"),
    ("ix_customer_phone_group_id", "ix_contact_phone_group_id"),
    ("ix_customer_phone_phone_normalised", "ix_contact_phone_phone_normalised"),
    ("ix_customer_role_customer_id", "ix_contact_role_contact_id"),
    ("ix_customer_role_group_id", "ix_contact_role_group_id"),
    ("uq_customer_role_customer_id_role_open", "uq_contact_role_contact_id_role_open"),
    ("ix_customer_stock_item_booking_item_occurred", "ix_contact_stock_item_booking_item_occurred"),
    ("ix_customer_tag_customer_id", "ix_contact_tag_contact_id"),
    ("ix_customer_tag_group_id", "ix_contact_tag_group_id"),
    ("ix_legal_basis_customer_id", "ix_legal_basis_contact_id"),
    ("ix_vehicle_party_customer_id", "ix_vehicle_party_contact_id"),
)

# (table, column, comment after, comment before) — the owner comments that name the context
_COMMENTS = (
    (
        "vehicle_party",
        "contact_id",
        "Same context (contact) as this table. No DB-level FK (PR-2, ADR-015) — named explicitly in scope.",
        "Same context (customer) as this table. No DB-level FK (PR-2, ADR-015) — named explicitly in scope.",
    ),
    (
        "vehicle_party",
        "merged_into_contact_id",
        "Same context (contact): the survivor this row's contact was merged into, set only on a copy the merge kept closed because the survivor already held its (vehicle, role, effective_from). KAN-177.",
        "Same context (customer): the survivor this row's customer was merged into, set only on a copy the merge kept closed because the survivor already held its (vehicle, role, effective_from). KAN-177.",
    ),
    (
        "vehicle_party",
        "merged_into_vehicle_id",
        "Owned by the vehicle context (vehicle_mdm.id, no DB-level FK): the vehicle this row's vehicle was merged into, set only on a copy the vehicle merge kept closed because the survivor already carried its (contact, role, effective_from). KAN-672.",
        "Owned by the vehicle context (vehicle_mdm.id, no DB-level FK): the vehicle this row's vehicle was merged into, set only on a copy the vehicle merge kept closed because the survivor already carried its (customer, role, effective_from). KAN-672.",
    ),
    (
        "vehicle_party",
        "closed_for_party_id",
        "Same context (contact): the planned vehicle_party row whose start this row was closed at (KAN-644); cleared when that handover is cancelled. KAN-583.",
        "Same context (customer): the planned vehicle_party row whose start this row was closed at (KAN-644); cleared when that handover is cancelled. KAN-583.",
    ),
)


def _comment(table: str, column: str, text: str | None) -> None:
    quoted = "NULL" if text is None else "'" + text.replace("'", "''") + "'"
    op.execute(f'COMMENT ON COLUMN "{table}"."{column}" IS {quoted}')


def upgrade() -> None:
    for old, new in _TABLES:
        op.rename_table(old, new)
    for table, old, new in _COLUMNS:
        op.alter_column(table, old, new_column_name=new)
    for table, old, new in _CONSTRAINTS:
        op.execute(f'ALTER TABLE "{table}" RENAME CONSTRAINT "{old}" TO "{new}"')
    for old, new in _INDEXES:
        op.execute(f'ALTER INDEX "{old}" RENAME TO "{new}"')
    for table, column, after, _before in _COMMENTS:
        _comment(table, column, after)


def downgrade() -> None:
    for table, column, _after, before in _COMMENTS:
        _comment(table, column, before)
    for old, new in _INDEXES:
        op.execute(f'ALTER INDEX "{new}" RENAME TO "{old}"')
    for table, old, new in _CONSTRAINTS:
        op.execute(f'ALTER TABLE "{table}" RENAME CONSTRAINT "{new}" TO "{old}"')
    for table, old, new in _COLUMNS:
        op.alter_column(table, new, new_column_name=old)
    for old, new in _TABLES:
        op.rename_table(new, old)
