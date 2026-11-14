/**
 * KAN-430 — the permission-matrix rows a screen asks about before it offers
 * a write (`useCanWrite`). Names only, never which role holds them: the
 * answer is the server's (`LoginResponse.writableCapabilities`, from
 * `CAPABILITY_MATRIX`). A name is added here when a screen first asks about
 * it; `tests/test_writable_capabilities.py` checks every one is a matrix row.
 * A module of its own, importing nothing, so test fixtures can read it.
 */
export const GATED_CAPABILITIES = [
  'branding',
  'configurations',
  'customer_vehicle_links',
  'dealership_settings',
  'document_templates',
  'locations',
  'marketplace_publishing',
  'number_ranges',
  'reference_lists',
  'sales_contracts',
  'sales_offers',
  'stock_acquisition',
  'stock_condition',
  'stock_items',
  'stock_vin_correction',
  'ui_translations',
  'valuation_use_reversal',
] as const
export type GatedCapability = (typeof GATED_CAPABILITIES)[number]
