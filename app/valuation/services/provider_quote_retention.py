"""KAN-599 — how long a provider quote is kept (Anto, 2026-11-13: option A,
«the quote stays as long as its valuation»).

A `ValuationProviderQuote` is stored on the advisor's click («Bewertung von
auto-i-dat holen», KAN-552) before the valuation is created, and holds the
car's first registration and mileage (revDSG: personal vehicle facts). Many
are never used — the advisor fetched again, or never saved the valuation.

The ruling, as built:

- **No maximum age** (Anto, 2026-11-11). A quote of any age may back a new
  valuation; nothing is refused for age. The seller decides when to refresh
  («Erneut holen», with the quote's date on the dialog — KAN-552).
- **A quote a valuation references stays as long as that valuation.** It goes
  when the valuation goes, under the valuation's own retention (PRD-Vehicles
  FR-V-17, not built yet; that work deletes both together). The foreign key
  `fk_valuation_provider_quote_id` (no `ON DELETE`) makes the database refuse
  the delete of a referenced quote, whatever this module gets wrong.
- **A quote no valuation references is deleted `RETENTION_YEARS` after its
  `fetched_at`**, by the daily job `valuation.provider_quotes.purge_unused`
  (`app/valuation/daily_jobs.py`), with one audit row each (`delete`, reason
  `PURGE_REASON`, the quote's own Betrieb as tenant, no actor — the system).
  The audit row names what the quote was, never what it held: no mileage,
  first registration, FzKey or option keys. The quote's earlier audit row
  (`create`) holds none of them either, so nothing needs redacting (ADR-121
  is not touched). No event: no other context reads quotes.

ADR-066 is untouched: a valuation is never edited; a quote is written once
and only ever deleted whole.

**The race with a valuation create** (KAN-703): the create reads its quote
`FOR KEY SHARE` (`provider_quote.lock_quote_for_reference_or_404`) and holds
the lock to its commit; the job locks each quote `FOR UPDATE SKIP LOCKED`, so
a quote a create holds is skipped, and then re-reads the references in a fresh
statement, so a valuation committed before the lock is seen and its quote
kept. A create that reaches a quote the job holds waits for the job's commit
and finds no row: the 404 any vanished quote gets (the advisor fetches a new
one), never a foreign-key failure at the insert; nothing is stored.
"""

import dataclasses
import datetime as dt

from sqlalchemy import exists, select
from sqlalchemy.orm import Session

from app.core.audit import record_audit_event
from app.core.base import utcnow
from app.valuation.models.valuation import Valuation, ValuationProviderQuote

# KAN-599 — Anto, 2026-11-13 (option A): a quote no valuation references is
# deleted ten years after it was fetched. Calendar years, never fewer.
RETENTION_YEARS = 10
BATCH_SIZE = 200
PURGE_REASON = "unused_quote_retention_purged"
ENTITY_TYPE = "valuation_provider_quote"


@dataclasses.dataclass(frozen=True)
class PurgeResult:
    purged: int


def retention_cutoff(now: dt.datetime) -> dt.datetime:
    """`now` minus `RETENTION_YEARS` calendar years. On 29 February the
    cutoff falls on the 28th (earlier), so a quote is never deleted before
    it is ten years old."""

    try:
        return now.replace(year=now.year - RETENTION_YEARS)
    except ValueError:
        return now.replace(year=now.year - RETENTION_YEARS, day=28)


def purge_unused_quotes(
    db: Session, *, now: dt.datetime | None = None, batch_size: int = BATCH_SIZE
) -> PurgeResult:
    """Every Betrieb's quotes, oldest first, in batches: the ones fetched on
    or before the cutoff that no valuation references are locked (`SKIP
    LOCKED`), the references re-read in a fresh statement, and the still
    unreferenced ones deleted with their audit rows; one commit per batch.
    A repeat run finds nothing to do."""

    cutoff = retention_cutoff(now or utcnow())
    unreferenced = ~exists().where(Valuation.provider_quote_id == ValuationProviderQuote.id)
    purged = 0
    while True:
        locked = list(
            db.scalars(
                select(ValuationProviderQuote)
                .where(ValuationProviderQuote.fetched_at <= cutoff, unreferenced)
                .order_by(ValuationProviderQuote.fetched_at, ValuationProviderQuote.id)
                .limit(batch_size)
                .with_for_update(skip_locked=True, of=ValuationProviderQuote)
                .execution_options(populate_existing=True)
            )
        )
        if not locked:
            break
        # A fresh statement sees a valuation committed after the read above
        # took its snapshot (READ COMMITTED); its quote is kept.
        now_referenced = set(
            db.scalars(
                select(Valuation.provider_quote_id).where(
                    Valuation.provider_quote_id.in_([quote.id for quote in locked])
                )
            )
        )
        doomed = [quote for quote in locked if quote.id not in now_referenced]
        if doomed:
            purged += _delete(db, doomed)
        # One referenced since the first read no longer matches it, so the
        # next read cannot return it again: the loop always moves on.
        db.commit()
    return PurgeResult(purged=purged)


def _delete(db: Session, quotes: list[ValuationProviderQuote]) -> int:
    for quote in quotes:
        record_audit_event(
            db,
            entity_type=ENTITY_TYPE,
            entity_id=quote.id,
            # The quote's own Betrieb: the job runs across every tenant.
            tenant_id=quote.tenant_id,
            action="delete",
            actor_id=None,
            # What the quote was, never what it held: no mileage, first
            # registration, FzKey or option keys — a copy here would outlive
            # the purge it records.
            before={
                "configurationId": str(quote.configuration_id),
                "providerRequestRef": str(quote.provider_request_ref),
                "fetchedAt": quote.fetched_at.isoformat(),
            },
            reason=PURGE_REASON,
        )
        db.delete(quote)
    db.flush()
    return len(quotes)
