// The contact module's one door (ADR-161, KAN-636). Production code outside
// src/modules/contact/ imports what it needs from here, never from a file
// inside; the router (App.tsx) lazy-loads the pages by path so each stays its
// own chunk, and tests may reach in to render what they test. A door never
// names a page (rule 4): ContactDetailContent, which the Vehicle 360, the
// valuation detail, the contract page and the offer workspace overlay, lives
// in components/. What the shell or several modules use is in src/shared/.
// architecture/module-boundaries.test.ts enforces it.
export { ContactCreateDialog } from './components/ContactCreateDialog'
export { ContactDetailContent } from './components/contact-detail/ContactDetailContent'
