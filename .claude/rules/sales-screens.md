---
paths:
  - "frontend/apps/dms/src/pages/Offer*"
  - "frontend/apps/dms/src/pages/Contract*"
  - "frontend/apps/dms/src/pages/SalesListPage*"
  - "frontend/apps/dms/src/pages/StockDetailPage.salesActions*"
  - "frontend/apps/dms/src/pages/*.createOffer.render.test.tsx"
  - "frontend/apps/dms/src/modules/*/pages/*.createOffer.render.test.tsx"
  - "frontend/apps/dms/src/modules/customer/pages/customerScreens.salesEntitlement*"
  - "frontend/apps/dms/src/components/deal-workspace/**"
  - "frontend/apps/dms/src/components/contract-detail/**"
  - "frontend/apps/dms/src/components/dealRowMenu.tsx"
  - "frontend/apps/dms/src/components/PriceBuildUp.tsx"
  - "frontend/apps/dms/src/components/Offer*"
  - "frontend/apps/dms/src/components/SalesDocumentsSection*"
  - "frontend/apps/dms/src/modules/valuation/components/CreateOfferFromValuationAlert.tsx"
  - "frontend/apps/dms/src/modules/customer/components/customer-detail/OffersContractsTab.tsx"
  - "frontend/apps/dms/src/modules/customer/components/customer-detail/offersContracts*"
  - "frontend/apps/dms/src/hooks/useDebouncedNumberField.ts"
  - "frontend/apps/dms/src/hooks/useDealSaveQueue.ts"
  - "frontend/apps/dms/src/modules/valuation/hooks/useCreateOfferFromValuation.ts"
  - "frontend/apps/dms/src/salesOptions.ts"
  - "frontend/packages/ui-kit/src/components/Sales*"
  - "frontend/packages/ui-kit/src/components/DocumentPreview.tsx"
---
<!-- Maintainer note. PRD-Sales v2, ADR-059, ADR-061, ADR-063, ADR-083. Moved out of sales.md by
KAN-615 (2026-11-13); listed by file until the frontend has module folders (KAN-611). Last verified
against main@73baf3f (2026-10-25). The backend rules are sales.md. -->

# Sales screens: the offer workspace, the contract page, the Sales list

- **Both deal workspaces send their saves through `hooks/useDealSaveQueue.ts`**: one after the
  other, each with the version the previous one returned, a refused save said and never retried
  (the server refuses the second of two saves sent with one version).
- **Nothing is signed or generated over an unsaved figure** (ADR-063): «Vertrag bestätigen» stays
  disabled while a price edit is unsaved or any deal save is in flight; «Offerte generieren» waits
  for an unsaved price or leasing value and for queued saves, and says why it is disabled.
- **The five pricing inputs never send a negative figure** (base price, discount, the three
  leasing figures): `useDebouncedNumberField`'s `nonNegative` holds one like a partial entry —
  shown as typed, marked `common.notNegative`, never committed. The inputs still take «-» (an
  input refusing it would empty itself, and that clear would be saved). A price of CHF 0.00 still
  shows the pricing container as "in progress".
- **An offer with a live contract** (sales.md) renders read-only in the draft workspace — disabled
  fieldsets over every write control, «Offerte generieren» disabled with the reason — under a
  banner naming the contract with «Vertrag öffnen»; a save refused `offer_has_contract` is worded
  in that banner's sentence. What only shows stays live: the customer, vehicle and pricing
  containers take `readOnly` themselves (`LockedWhile.tsx` around their writes), so the customer's
  overlay, the stock number and «zum Lagerfahrzeug» still open. On a sent offer the seller picker
  and «Übernehmen» are disabled before the click (`OfferRead.liveContractId`/`Number`), and
  «Dokument erzeugen» is disabled and says why.
- **The responsible seller:** `DealOwnerSelect` on the offer workspace, the sent offer and the
  pending contract; «Übernehmen» is one action (`components/dealRowMenu.tsx`) in the Sales list's
  row menu and both detail overflows (ADR-061) and takes an unassigned open deal for the signed-in
  user. The Sales list has the «Nicht zugewiesen» chip and the «Verantwortlich» column.
- **The stock number is on screen** (Anto): «Lagernummer S-…» (`StockNumberLink`) beside the car
  in the offer workspace's and the pending contract's vehicle container and in the header of a sent
  offer and of a contract past editing, opening the stock detail as an overlay (ADR-059); on the
  Sales grid an optional «Lagernummer» column, hidden by default, sortable.
- **The buyer is picked inside a pending contract** as on the offer (`DealCustomerContainer`,
  shared). The stock detail's «Vertrag erstellen» creates a pending contract with the car;
  «Offerte erstellen» creates an offer and then sets the same car.
- **«Rechnung anfordern»** is the contract page's primary action on a confirmed contract
  (`RequestInvoiceDialog`: the plate and canton, or «ohne Kontrollschild»; a plate current on
  another car asks before it moves). Once requested it stays visible but disabled, naming the day.
