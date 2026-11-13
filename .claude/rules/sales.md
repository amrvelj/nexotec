---
paths:
  - "app/sales/**"
  - "tests/test_sales_*.py"
  - "tests/architecture/test_margin_never_in_rendered_document.py"
  - "tests/architecture/test_adr_047_no_shared_transaction.py"
  - "alembic/versions/sales/**"
---
<!-- Maintainer note. PRD-Sales v2 (S-D10); ADR-041/046/047/049/050/051/052/057/063/065/083/098/109.
KAN-615 (2026-11-13) cut it to the invariants: screens in sales-screens.md, reconciliation in
sales-reconciliation.md. Last verified against main@73baf3f (2026-10-25). -->

# Sales: offers, contracts, pricing, documents

## Events and boundaries
- **ADR-046 — two contract events:** `sales.contract.confirmed` at signature (it carries the pricing
  snapshot) and `sales.contract.invoice_requested` at hand-off — never one name for both. **The
  hand-off carries the plate the car leaves with** (Anto, KAN-523): checked first against the
  group's plate register (`vehicle.public.check_plate_handover`, its own session); a plate current
  on another car is refused, naming it through the audited plate-read guard, until the user
  confirms the move; the audit row says whether a plate went, never its value. **Once per
  contract** (Anto, KAN-539): `invoice_requested_at` is stamped in the outbox row's transaction and
  a second request is refused before any write — contracts requested before the stamp existed too.
- **ADR-047** (rule 12); the one ruled exception is number allocation (ADR-109, below).
- **ADR-052 — `is_invoiceable` is Stock's fact.** Sales keeps a replica **per stock item**
  (`sales_stock_item_purchase`, from `inventory.stock_item.purchased`) and never queries Stock on
  the invoicing path; `SalesContract.is_invoiceable` is **derived on read**, no stored column.
  `inventory.stock_item.storno` deletes the replica row (locked first) unless Stock holds the car as
  bought again — re-read through `inventory.public.is_stock_item_purchased`, since delivery is
  unordered; only that consumer reads asynchronously, `request_invoice` reads the replica alone
  (ADR-052 addendum). An offer and a contract on a stock car carry its stock number (rule 2's pair),
  copied once when the car is put on the deal, cleared with `stock_item_id`, taken by a contract
  from the offer and a copy, never refreshed. Every cross-context id on a Sales table is
  reconciled (`sales-reconciliation.md`).
- **ADR-050 — `sales_contract` supersedes the legacy `transaction` table** (retired for writes;
  removal KAN-555). A legacy contract stays `CONFIRMED`; `request_invoice` and `cancel_contract`
  refuse it (`legacy_sale`: the old system invoiced it), so its offer never frees
  (`test_sales_contract_legacy_refusal.py`).
- **A contract starts from exactly one of an offer, a customer or a stock car** — never empty.
  From a car it is a pending contract with that car (Path A, in the creating transaction); the
  buyer is picked inside it through the one `offer.apply_customer_change` both hosts use.
- **No contract is signed without a buyer** (`missing_customer`, before the vehicle and price
  gates). A pending contract priced without inputs takes a PATCH carrying `customerId` alone or
  `ownerUserId` alone, price untouched, and can still be confirmed or cancelled; any other field is
  `contract_priced_without_inputs`.
- **One live contract per offer** (`offer_already_contracted`, under the offer's row lock); a
  cancelled contract frees it. **An offer with a live contract is read-only** (Anto, KAN-408):
  every write and its document generation are 409 `offer_has_contract` — the contract is edited
  itself while pending (ADR-083), so the two never disagree.
- **The deal row follows its deal's live entity** (`deal_projection.py`): the live contract while
  there is one, else the offer; a cancelled contract hands the row back to its offer, a contract
  without an offer keeps its own row. Generating a document re-projects it.
- **Row locks offer → contract → deal**; `upsert_deal_projection` flushes its caller's changes
  first, the customer merge's repointing flushes in the same order, and an offer's or contract's
  row is written or locked (`priced_deal.lock_editable`) before its line items.

## The responsible seller (Anto)
- `owner_user_id` + label on offer, contract and deal (rule 2): the creator by default when they
  act in the Betrieb (`platform.public.get_active_user_acting_in` — homed or an active member,
  active or invited; the customer advisor's rule too), a colleague after a hand-over, nobody once a
  leaver's deals went to the unassigned queue (open offers and pending contracts; drafts stay
  hidden). `created_by` stays who typed it (A-RULE-5). The PATCH takes `ownerUserId` (422 for
  someone who cannot act there); alone, also on an open offer without a live contract and on a
  pending contract priced without inputs. A contract takes its offer's seller only while that
  seller is active or invited, else its creator. `services/ownership.py` is Sales' ownership
  provider (`platform-users.md`, D-A-35). The labels follow a rename overnight
  (`events-worker.md`); the buyer's name and language move on open work only, signed work keeps
  its name, and an offer with a live contract of its own buyer mirrors it. `customer_locality`
  stays unfilled (ruled).

## Price and VAT (ADR-057)
One gross price, the VAT line on the printed document only, ADR-098, the fiktiver Vorsteuerabzug,
the pricing bounds and the discount cap: `sales-pricing.md`. **There is no `vatTreatment`.**

## Contract rules
- **Purchase gate at invoicing, not at confirmation** (ADR-052, K-12): `request_invoice` refuses a
  car the replica shows as not bought. A contract may be confirmed — and the car reserved — first.
  Confirming a manual configuration makes a pipeline item, created reserved by Stock and released
  on `sales.contract.cancelled`; Sales records it on the contract from `inventory.stock_item.added`
  (`vehicle_source` stays `manual`) and makes no reservation call for it.
- A contract needs a vehicle and a price before confirmation and before its document
  (`services/contract_completeness.py`, one rule). **CHF 0.00 is a price**; only an empty one is
  `missing_price`.
- **A pending contract is editable, a confirmed one frozen** (ADR-083): its edits run the offer's
  own code (`PricedDeal`, `deal_editing` — the one vehicle-and-price edit), it holds the offer's
  pricing inputs and line items, and a non-pending one refuses every write. **Never give the
  contract a second copy of a pricing rule.**
- **Every offer and contract route that changes the row loads it `for_update`**, so two saves run
  one after the other and the second is a stale-version 409 — If-Match alone would let an edit
  land after signing. A trade-in save is one transaction holding that lock to its commit
  (`test_sales_trade_in_lock.py`).
- **A failed confirmation compensates** (releases its reservation, reverts "used") under the
  contract's row lock on a session of its own, after ending its own transactions; it undoes
  nothing once the contract is signed or when it cannot take the lock (logged, left to the
  nightly run). Confirmation consumes the trade-in valuation first, then reserves the car
  (`valuation-trade-in.md`); an expired one refuses it, and a car that left stock cannot be signed
  again (`vehicle_left_stock`).
- An edit publishes no event — except a deal starting to hold a configuration id, which publishes
  `sales.{offer,contract}.configuration_attached` in its own transaction (`vehicle-configurator.md`).
- `TradeInRequest.customerId` is deprecated: ignored but for its 404 (Anto, KAN-562;
  `test_sales_trade_in.py`).
- **ADR-065** — a credit block stops contract confirmation, not the offer; an address-less
  customer is gated at confirmation (D-20). Do-not-contact refuses the offer, a contract straight
  from the customer, an attach and every confirmation; a merged customer every attach.

## Documents
- **ADR-063** — build, then review the rendered document in the **customer's correspondence
  language** with the seller-only margin panel beside it, never on it
  (`test_margin_never_in_rendered_document.py`). No customer language: the Betrieb's own default
  (Customers FR-13), never a silent German.
- **The wording is the Betrieb's standard text**: offer intro and footer, contract footer, through
  `platform.public.render_standard_text` with Sales' facts; a token Sales cannot fill fails the
  generation before anything is written. Sales holds no text column (ADR-051,
  `platform-documents.md`).
- **ADR-041** — an offer, and a pending contract through the same function, freeze a
  `vehicle_snapshot`. **Path B** is the configurator, `build` only: `configurationId` prefills the
  base price, freezes the spec block and turns its price lines into factory-option lines,
  re-frozen on a new configuration version while editable; it travels to the contract and onto the
  pipeline item. **A copied offer gets a configuration of its own** (FR-C-11, copied on its own
  session and discarded if the offer write fails); the trade-in keeps the one its valuation was
  judged on and has no vehicle-mdm record. The offer document itemises factory-option lines, the
  contract's does not; a stock car's lines carry four texts
  (`test_kan665_stock_options_four_languages.py`).

## Visibility and numbers
- **ADR-049** — full commercial visibility inside the dealership; **ADR-029** at the group boundary.
- Offer and contract numbers are working business keys; the gapless legal number is the
  invoice's (ADR-032, `finance.md`). The contract's comes from Sales' own counter inside the
  caller's transaction. **The offer's comes from the Betrieb's `offer` range** (ADR-109,
  `platform.public.allocate_number`, inside the offer's transaction), continuing Sales' old
  counter: until KAN-540 retires it that counter is locked first, passed as `continue_after` and
  kept in step (lock order: Sales' counter → Platform's range).
