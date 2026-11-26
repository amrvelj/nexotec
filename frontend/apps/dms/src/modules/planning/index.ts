// The planning module's one door (ADR-161, KAN-636; KAN-809). Production code
// outside src/modules/planning/ imports what it needs from here, never from a
// file inside; the router (App.tsx) lazy-loads the page by path so it stays its
// own chunk, and tests may reach in to render what they test.
// architecture/module-boundaries.test.ts enforces it. Nothing is shared yet.
export {}
