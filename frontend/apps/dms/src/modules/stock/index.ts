// The stock module's one door (ADR-161, KAN-636). Production code outside
// src/modules/stock/ imports what it needs from here, never from a file
// inside; the router (App.tsx) lazy-loads the pages by path so each stays its
// own chunk, and tests may reach in to render what they test. A door never
// names a page (rule 4): StockDetailContent, which the offer detail, the offer
// workspace and the contract page overlay, lives in components/stock-detail/.
// What the shell or several modules use is in src/shared/.
// architecture/module-boundaries.test.ts enforces it.
export { StockDetailContent } from './components/stock-detail/StockDetailContent'
export { stockOpenState } from './components/stockRowMenu'
