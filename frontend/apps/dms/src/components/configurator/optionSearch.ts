import type { CatalogueOptionRead } from '../../api/types'

/** KAN-211 (Anto, 2026-11-08): the advisor finds an option by its text or by
 * a code — the manufacturer's (`manufacturerCode`, what the order sheet and
 * the customer name) or auto-i's own key. Case-insensitive substring. */
export function optionMatchesSearch(option: CatalogueOptionRead, search: string): boolean {
  const needle = search.trim().toLowerCase()
  if (needle === '') return true
  return [option.description, option.manufacturerCode, option.optionCode].some(
    (text) => typeof text === 'string' && text.toLowerCase().includes(needle),
  )
}
