"""The migration smoke test's vehicle catalogue chain (CI only), split from
`scripts/seed_migration_smoke_test.py` (KAN-688: the source-file ratchet scans
`scripts/` too). The seed imports and calls it; it writes vehicle-context
tables only and never commits — the seed's one commit does.

Run as part of `python scripts/seed_migration_smoke_test.py`, which puts
`scripts/` first on `sys.path`.
"""

import datetime as dt

import sqlalchemy as sa
from sqlalchemy import inspect
from sqlalchemy.orm import Session

from app.core.base import utcnow
from app.core.types import GUID
from app.core.uuid7 import uuid7
from app.vehicle.models.catalogue import Brand, ModelGroup, ModelVariant, TypeApproval, VariantTypeApproval

# Fixed, known values the companion verify script looks rows up by.
_CATALOGUE_BRAND_CODE = "smoke-catalogue"
_CATALOGUE_VARIANT_NAME = "Smoke Variant 1.4 TB"
_CATALOGUE_TYPE_APPROVAL_NUMBER = "SMK001"
_CATALOGUE_FIRST_REGISTRATION_FROM = dt.date(2015, 6, 1)


def seed_catalogue_type_approval(db: Session) -> None:
    """A brand → model group → model variant → type approval chain, so the
    type-approval-m2m migration (rev eb660a3213bd) exercises its backfill
    against a populated `vehicle_type_approval` table.

    Runs against three schema states, same reasoning as the dealer/customer
    chains above (this PR's ORM classes only know the newest shape):
      1. pre-m2m (`vehicle_type_approval.model_variant_id` present, UNIQUE
         `type_approval_number`) — raw inserts, one-to-one type approval;
      2. post-m2m, **pre-C-A** — the link table exists but
         `vehicle_model_variant` does not yet have KAN-39's spec block, so
         the `ModelVariant` ORM class (which now carries ~38 extra columns)
         cannot insert here; raw-insert just the pre-C-A variant columns;
         the same holds for any later column the class gains that main's
         schema lacks (KAN-587's `homologation_class`), so the test is
         "does the table hold every column the class maps";
      3. post-C-A (this PR's own heads) — schema and code agree, full ORM.
    `migration-upgrade-from-previous` hits state 2; `migration-smoke-test`
    hits state 3.
    """

    now = utcnow()
    brand_id, group_id, variant_id = uuid7(), uuid7(), uuid7()

    old_shape = "model_variant_id" in {
        col["name"] for col in inspect(db.get_bind()).get_columns("vehicle_type_approval")
    }

    if old_shape:
        db.execute(
            sa.table(
                "vehicle_brand",
                sa.column("id", GUID()),
                sa.column("code", sa.String()),
                sa.column("display_name", sa.String()),
                sa.column("version", sa.Integer()),
                sa.column("created_at", sa.DateTime(timezone=True)),
                sa.column("updated_at", sa.DateTime(timezone=True)),
            )
            .insert()
            .values(
                id=brand_id,
                code=_CATALOGUE_BRAND_CODE,
                display_name="Smoke Catalogue",
                version=1,
                created_at=now,
                updated_at=now,
            )
        )
        db.execute(
            sa.table(
                "vehicle_model_group",
                sa.column("id", GUID()),
                sa.column("brand_id", GUID()),
                sa.column("name", sa.String()),
                sa.column("created_at", sa.DateTime(timezone=True)),
                sa.column("updated_at", sa.DateTime(timezone=True)),
            )
            .insert()
            .values(id=group_id, brand_id=brand_id, name="Smoke Group", created_at=now, updated_at=now)
        )
        db.execute(
            sa.table(
                "vehicle_model_variant",
                sa.column("id", GUID()),
                sa.column("model_group_id", GUID()),
                sa.column("name", sa.String()),
                sa.column("model_year_from", sa.Integer()),
                sa.column("version", sa.Integer()),
                sa.column("created_at", sa.DateTime(timezone=True)),
                sa.column("updated_at", sa.DateTime(timezone=True)),
            )
            .insert()
            .values(
                id=variant_id,
                model_group_id=group_id,
                name=_CATALOGUE_VARIANT_NAME,
                model_year_from=2015,
                version=1,
                created_at=now,
                updated_at=now,
            )
        )
        db.execute(
            sa.table(
                "vehicle_type_approval",
                sa.column("id", GUID()),
                sa.column("model_variant_id", GUID()),
                sa.column("type_approval_number", sa.String()),
                sa.column("first_registration_from", sa.Date()),
                sa.column("created_at", sa.DateTime(timezone=True)),
                sa.column("updated_at", sa.DateTime(timezone=True)),
            )
            .insert()
            .values(
                id=uuid7(),
                model_variant_id=variant_id,
                type_approval_number=_CATALOGUE_TYPE_APPROVAL_NUMBER,
                first_registration_from=_CATALOGUE_FIRST_REGISTRATION_FROM,
                created_at=now,
                updated_at=now,
            )
        )
        db.flush()
        return

    # State 2 vs 3: the m2m link table exists either way, but KAN-39's spec
    # block on `vehicle_model_variant` may not — `migration-upgrade-from-
    # previous` seeds against main's schema before this PR's columns land.
    # Judged by every column the ORM class maps, not one marker: a later PR
    # that adds a spec-block column (KAN-587's `homologation_class`) meets
    # main's schema without it in exactly the same way.
    variant_columns = {col["name"] for col in inspect(db.get_bind()).get_columns("vehicle_model_variant")}
    variant_has_spec_block = {column.name for column in ModelVariant.__table__.columns} <= variant_columns

    if not variant_has_spec_block:
        # State 2 — Brand/ModelGroup/TypeApproval ORM classes are unchanged
        # and fine; only ModelVariant grew columns this schema lacks, so
        # raw-insert its pre-C-A shape.
        brand = Brand(id=brand_id, code=_CATALOGUE_BRAND_CODE, display_name="Smoke Catalogue")
        db.add(brand)
        db.flush()
        group = ModelGroup(id=group_id, brand_id=brand_id, name="Smoke Group")
        db.add(group)
        db.flush()
        db.execute(
            sa.table(
                "vehicle_model_variant",
                sa.column("id", GUID()),
                sa.column("model_group_id", GUID()),
                sa.column("name", sa.String()),
                sa.column("model_year_from", sa.Integer()),
                sa.column("version", sa.Integer()),
                sa.column("created_at", sa.DateTime(timezone=True)),
                sa.column("updated_at", sa.DateTime(timezone=True)),
            )
            .insert()
            .values(
                id=variant_id,
                model_group_id=group_id,
                name=_CATALOGUE_VARIANT_NAME,
                model_year_from=2015,
                version=1,
                created_at=now,
                updated_at=now,
            )
        )
        db.flush()
        db.add(
            TypeApproval(
                type_approval_number=_CATALOGUE_TYPE_APPROVAL_NUMBER,
                variant_links=[
                    VariantTypeApproval(
                        model_variant_id=variant_id,
                        first_registration_from=_CATALOGUE_FIRST_REGISTRATION_FROM,
                    )
                ],
            )
        )
        db.flush()
        return

    # State 3 — schema and this PR's code agree.
    brand = Brand(id=brand_id, code=_CATALOGUE_BRAND_CODE, display_name="Smoke Catalogue")
    db.add(brand)
    db.flush()
    group = ModelGroup(id=group_id, brand_id=brand_id, name="Smoke Group")
    db.add(group)
    db.flush()
    variant = ModelVariant(
        id=variant_id, model_group_id=group_id, name=_CATALOGUE_VARIANT_NAME, model_year_from=2015
    )
    db.add(variant)
    db.flush()
    db.add(
        TypeApproval(
            type_approval_number=_CATALOGUE_TYPE_APPROVAL_NUMBER,
            variant_links=[
                VariantTypeApproval(
                    model_variant_id=variant_id,
                    first_registration_from=_CATALOGUE_FIRST_REGISTRATION_FROM,
                )
            ],
        )
    )
    db.flush()
