import type { CatalogueOptionRead } from '../../api/types'

/** KAN-211 (Anto, 2026-11-08): the advisor finds an option by its text or by
 * a code — the manufacturer's (`manufacturerCode`, what the order sheet and
 * the customer name) or auto-i's own key. Case-insensitive substring. */
export function optionMatchesSearch(option: CatalogueOptionRead, search: string): boolean {
  const needle = search.trim().toLowerCase()
  if (needle === '') return true
  // KAN-595: the name in every language the provider delivered is searchable,
  // so a French-speaking advisor finds «Pompe à chaleur» as well as «Wärmepumpe».
  const names = Object.values(option.descriptions ?? {})
  return [option.description, ...names, option.manufacturerCode, option.optionCode].some(
    (text) => typeof text === 'string' && text.toLowerCase().includes(needle),
  )
}
