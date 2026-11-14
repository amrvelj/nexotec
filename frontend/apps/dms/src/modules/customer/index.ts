// The customer module's one door (ADR-161, KAN-636). Production code outside
// src/modules/customer/ imports what it needs from here, never from a file
// inside; the router (App.tsx) lazy-loads the pages by path so each stays its
// own chunk, and tests may reach in to render what they test. A door never
// names a page (rule 4): CustomerDetailContent, which the Vehicle 360, the
// valuation detail, the contract page and the offer workspace overlay, lives
// in components/. What the shell or several modules use is in src/shared/.
// architecture/module-boundaries.test.ts enforces it.
export { CustomerCreateDialog } from './components/CustomerCreateDialog'
export { CustomerDetailContent } from './components/customer-detail/CustomerDetailContent'
