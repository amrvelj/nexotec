---
paths:
  - "tests/architecture/**"
  - "frontend/apps/dms/src/architecture/**"
---
<!-- Maintainer note. Moved out of tests.md by KAN-615 (2026-11-13): the enumeration of what the
architecture tests guard became the directory itself; kept here is only what a test's name does
not say. Last verified against main@73baf3f (2026-10-25). -->

# Architecture tests

`tests/architecture/` (and the SPA's `src/architecture/`) are rulings in executable form; the
file names say what each guards. Never weaken, skip or loosen one to make a change pass — the
change or the ruling is wrong; raise it. A new guard is proved red before it goes green. What
the names do not say:

- `test_adr_047_no_shared_transaction.py`: every cross-context call is classified and every
  cross-context write commits its own transaction. Exceptions by name only: number allocation
  (`platform.public.allocate_number`, ADR-109) and, in the separately scanned `scripts/`, the
  migration smoke test's two CI fixtures (ADR-140). A new one fails by name.
- `test_every_post_accepts_idempotency_key.py`: the nine ratified exemptions are logout,
  switch-dealership and the seven unauthenticated front-door POSTs; a new one is Anto's ruling
  by name.
- `test_versioned_mutations_take_if_match.py`: every PATCH/PUT/DELETE and every POST on an
  existing entity, unless listed with why; a PATCH/PUT/DELETE exemption must name an
  unversioned model.
- `test_no_password_credential_storage.py`: password-shaped columns live in `credential` alone
  and no second credential table outside the allow-list; a third realm is an ADR (D-A-07).
- `test_sales_routers_carry_entitlement_gate.py`: every route of a sales, inventory, valuation
  or configurator router behind the `sales` gate — and no other route.
- `test_standard_texts_write_honours_delegations.py`: no standard-text write behind the plain
  `require_write`; the delegation-aware gate only on a route whose path carries `{category}`.
- `test_catalogue_browse_is_a_bounded_live_query.py`: one provider request per page and per
  facet field, `page[number]` only in the adapter, no catalogue query in a loop (ADR-112).
- `test_no_bulk_catalogue_retrieval.py` also refuses a daily job reaching an integration provider
  through the gateway (a connected-account revoker is not one).
- `test_no_real_provider_account_values.py` names the values it refuses by hash only — never
  put a value in the guard.
- `test_context_dependency_direction.py`: calls go only down the layers (ADR-131); its debt list
  only shrinks. `test_import_contracts_cover_every_module.py`: a context's contract forbids every
  module but `public`, a new top-level module included.
- `test_migration_seed_sources_are_frozen.py`: a change to a frozen seed source is a new data
  migration; never update a digest (`migrations.md`).
