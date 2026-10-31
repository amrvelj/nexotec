/**
 * KAN-430 — the permission-matrix rows a screen asks about before it offers
 * a write (`useCanWrite`). Names only, never which role holds them: the
 * answer is the server's (`LoginResponse.writableCapabilities`, from
 * `CAPABILITY_MATRIX`). A name is added here when a screen first asks about
 * it; `tests/test_writable_capabilities.py` checks every one is a matrix row.
 * A module of its own, importing nothing, so test fixtures can read it.
 */
export const GATED_CAPABILITIES = [
  'canonical_taxonomy',
  'configurations',
  'marketplace_publishing',
  'stock_acquisition',
  'stock_items',
  'ui_translations',
] as const
export type GatedCapability = (typeof GATED_CAPABILITIES)[number]
