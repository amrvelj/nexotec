// The valuation module's one door (ADR-158, KAN-636). Production code outside
// src/modules/valuation/ imports what it needs from here, never from a file
// inside; the router (App.tsx) lazy-loads the pages by path so each stays its
// own chunk, and tests may reach in to render what they test.
// architecture/module-boundaries.test.ts enforces it.
export { ValuationCreateDialog } from './components/ValuationCreateDialog'
export { ValuationSourceMarker } from './components/ValuationSourceMarker'
