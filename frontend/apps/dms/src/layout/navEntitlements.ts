import type { NavGroupConfig } from '@nexotec/ui-kit'

// Which nav entries belong to a module entitlement (KAN-198, D-A-04). An
// entry whose module the active dealership is not licensed for is left out —
// presentation only: the API's 404 is the boundary, this keeps a workshop
// from seeing a stock screen it cannot use (D-A-10). Entries without a module
// (master data, administration) are never hidden; role-hiding stays out
// (Risk R-4).
const NAV_MODULE_BY_HREF: Record<string, string> = {
  '/sales': 'sales',
  '/valuations': 'sales',
  '/stock': 'sales',
  '/aftersales': 'aftersales',
  '/parts': 'parts',
  '/finance': 'finance',
  '/reporting': 'reporting',
}

export function filterNavGroupsByEntitlements(groups: NavGroupConfig[], entitlements: readonly string[]): NavGroupConfig[] {
  return groups
    .map((group) => ({
      ...group,
      items: group.items.filter((item) => {
        const module = NAV_MODULE_BY_HREF[item.href]
        return module === undefined || entitlements.includes(module)
      }),
    }))
    .filter((group) => group.items.length > 0)
}
