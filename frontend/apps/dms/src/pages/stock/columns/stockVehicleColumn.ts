/**
 * KAN-578 (Anto, 2026-11-07, option A) — the «Fahrzeug» column is the stock
 * grids' primary column (UI/UX Specification § Anatomy: the name, never
 * hideable), so it opens wide enough for make + model + version («Škoda
 * Octavia Combi 2.0 TDI RS 4x4») instead of an even share of the row
 * (~140 px at 1440 px). The same width as the Users list's primary column.
 * A width the user set in the column panel still wins (the kit's
 * `columnLayout.widths` override `meta.width`).
 */
export const STOCK_VEHICLE_COLUMN_WIDTH = 300
