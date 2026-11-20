"""Catalogue reads that cross the variant ↔ Typenschein many-to-many.

The forward direction (a variant's Typenscheine) is a plain relationship
walk; this module is where the *reverse* direction lives — the lookup
FR-C-02 step 4 renders its picker from: a Typenschein resolves to 1..n
model variants, never a guess.
"""

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.text_collation import TextSortKey
from app.vehicle.models.catalogue import ModelVariant, TypeApproval, VariantTypeApproval


def find_model_variants_by_type_approval(db: Session, type_approval_number: str) -> list[ModelVariant]:
    """Every model variant homologated under this Typenschein.

    Returns 0..n variants. `type_approval_number` is not unique, so more
    than one `TypeApproval` row may carry it; the join fans out over all of
    them, and each variant is returned once. Order is stable by variant name
    then id so a picker renders deterministically.
    """

    homologated = (
        select(VariantTypeApproval.model_variant_id)
        .join(TypeApproval, TypeApproval.id == VariantTypeApproval.type_approval_id)
        .where(TypeApproval.type_approval_number == type_approval_number)
    )
    # A semi-join, not a DISTINCT: Postgres refuses `SELECT DISTINCT … ORDER BY name COLLATE …`
    # (the collated key is not in the select list), and the name sorts under the collation
    # like every other word column (KAN-769, ADR-190).
    stmt = (
        select(ModelVariant)
        .where(ModelVariant.id.in_(homologated))
        .order_by(TextSortKey(ModelVariant.name), ModelVariant.id)
    )
    return list(db.scalars(stmt).all())
