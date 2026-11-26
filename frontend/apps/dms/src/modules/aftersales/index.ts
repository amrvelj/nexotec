// The aftersales module's one door (ADR-161, KAN-636; KAN-810): the workshop
// order. Production code outside src/modules/aftersales/ imports what it needs
// from here, never from a file inside; the router (App.tsx) lazy-loads the
// pages by path so each stays its own chunk, and tests may reach in to render
// what they test. A door never names a page and holds re-exports only.
// architecture/module-boundaries.test.ts enforces it.
export { OrderStateBadge } from './components/OrderStateBadge'
