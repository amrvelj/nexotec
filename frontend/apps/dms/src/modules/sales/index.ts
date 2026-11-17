// The sales module's one door (ADR-161, KAN-636): offers, contracts, the deal
// workspace and the sales list. Production code outside src/modules/sales/
// imports what it needs from here, never from a file inside; the router
// (App.tsx) lazy-loads the pages by path so each stays its own chunk, and
// tests may reach in to render what they test. A door never names a page
// (rule 4). What the shell or several modules use is in src/shared/ (the
// deals' save queue, which stock's publishing tab uses too, is
// shared/useSaveQueue.ts). architecture/module-boundaries.test.ts enforces it.
export { translatedSalesDealStatusLabel, translatedSalesEntityTypeLabel } from './salesOptions'
