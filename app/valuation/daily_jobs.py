"""KAN-599 (Anto, 2026-11-11): the valuation context's daily retention job
for provider quotes, registered in `app/worker.py`.

A `ValuationProviderQuote` (KAN-552) holds a car's first registration and
mileage as the advisor typed them. Anto's ruling: a quote is deleted ten
years after it was fetched, by a daily job of this context, each deletion
audited. Nothing is refused for age before that: a quote may back a
valuation however old it is, and the dialog's «Erneut holen» fetches a new
one when the seller wants it.

A quote a valuation references is not deleted here: the valuation keeps it
as the source of its provider figure (`valuation.provider_quote_id`, a
foreign key inside this context), and a valuation is never edited after its
creation (ADR-066), so its pointer cannot be cleared. Such a quote goes with
its valuation under the valuation's own retention (PRD-Vehicles FR-V-17,
not built yet). Retention, not a repair: it reads no other context.
"""

import datetime as dt
import logging
import uuid

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.core.audit import record_audit_event
from app.core.base import utcnow
from app.valuation.models.valuation import Valuation, ValuationProviderQuote

logger = logging.getLogger(__name__)

QUOTE_RETENTION_YEARS = 10
_BATCH = 500


def quote_retention_cutoff(now: dt.datetime) -> dt.datetime:
    """The instant ten calendar years before `now`; 29 February falls back
    to 28 February."""

    try:
        return now.replace(year=now.year - QUOTE_RETENTION_YEARS)
    except ValueError:
        return now.replace(year=now.year - QUOTE_RETENTION_YEARS, day=28)


def purge_expired_provider_quotes(db: Session, *, now: dt.datetime | None = None) -> int:
    """Deletes every provider quote fetched more than ten years ago that no
    valuation references, across tenants, with one audit row per quote
    (`provider_quote_purged`, its tenant, what was asked and when it was
    fetched — never the figures). Commits per batch; returns how many were
    deleted. Run again the same day, it finds nothing left."""

    cutoff = quote_retention_cutoff(now or utcnow())
    referenced = select(Valuation.provider_quote_id).where(Valuation.provider_quote_id.is_not(None))
    deleted = 0
    while True:
        batch = db.scalars(
            select(ValuationProviderQuote)
            .where(ValuationProviderQuote.fetched_at < cutoff, ValuationProviderQuote.id.not_in(referenced))
            .order_by(ValuationProviderQuote.id)
            .limit(_BATCH)
            .with_for_update(skip_locked=True)
        ).all()
        if not batch:
            break
        ids: list[uuid.UUID] = []
        for quote in batch:
            record_audit_event(
                db,
                entity_type="valuation_provider_quote",
                entity_id=quote.id,
                tenant_id=quote.tenant_id,
                action="provider_quote_purged",
                actor_id=None,
                before={
                    "configurationId": str(quote.configuration_id),
                    "fetchedAt": quote.fetched_at.isoformat(),
                },
                reason=f"retention_{QUOTE_RETENTION_YEARS}_years",
            )
            ids.append(quote.id)
        db.execute(delete(ValuationProviderQuote).where(ValuationProviderQuote.id.in_(ids)))
        db.commit()
        deleted += len(ids)
        if len(batch) < _BATCH:
            break
    return deleted


def run_daily_provider_quote_purge(db: Session) -> None:
    deleted = purge_expired_provider_quotes(db)
    if deleted:
        logger.info("valuation provider quote retention purge", extra={"deleted": deleted})
