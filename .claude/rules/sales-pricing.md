---
paths:
  - "app/sales/services/pricing.py"
  - "app/sales/services/priced_deal.py"
  - "app/sales/services/deal_editing.py"
  - "app/sales/services/line_items.py"
  - "app/sales/services/document.py"
  - "app/sales/services/snapshot.py"
  - "app/sales/schemas/offer.py"
  - "app/sales/schemas/contract.py"
  - "app/sales/schemas/line_item.py"
  - "tests/test_sales_pric*.py"
  - "tests/test_sales_discount_cap.py"
  - "tests/test_sales_offer_pricing_bounds.py"
  - "tests/test_sales_documents.py"
  - "tests/architecture/test_no_vat_treatment_field.py"
  - "frontend/apps/dms/src/components/PriceBuildUp.tsx"
  - "frontend/apps/dms/src/pages/OfferWorkspace.pricing*"
---
<!-- Maintainer note. ADR-033, ADR-053, ADR-057, ADR-098. Moved out of sales.md by KAN-615 (2026-11-13)
for the pricing and document files. Last verified against main@73baf3f (2026-10-25). -->

# Sales pricing and VAT (ADR-057)

- **One price: gross, CHF incl. MwSt**, after all discounts; no net line, no VAT breakdown on
  screen or on the customer document. **There is no `vatTreatment`.**
- VAT is **one line on the printed document only**, at `Dealership.vat_rate` (Notion's
  `dealer_settings.vat_rate` does not exist), directly under «Verkaufspreis», before the trade-in
  and «Zu bezahlen»; no `vat_rate`, no VAT line — never a guessed rate.
- **The VAT line is computed on the full gross price, never on `payable`** (ADR-098): the trade-in
  pays part of the price, it does not reduce it (CHF 36,000 with a CHF 10,000 trade-in at 8.1 %
  prints CHF 2,697.50). Open: no tax advisor has confirmed it (KAN-436); if one disagrees, ADR-098
  is reopened, never overridden in code. Generated documents keep their frozen figure.
- Of ADR-033/ADR-053 only the **fiktiver Vorsteuerabzug** (Art. 28a MWSTG) survives; margin
  taxation (Art. 24a) is out of v1. It is a purchase-side fact owned by Stock; Sales reads it
  through the stock item's pricing and never writes it. Margin nets the notional credit whether
  or not a landed cost was recorded.
- **Pricing bounds:** `OfferUpdate` takes `ContractUpdate`'s bounds (no negatives;
  `test_sales_offer_pricing_bounds.py`). **A discount never exceeds what it is taken off**
  (`pricing.resolve_discount` caps it at its base; the typed value is stored as typed).
