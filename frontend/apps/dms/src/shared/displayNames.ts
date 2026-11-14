import type { CustomerRead } from '../api/types'

/** A business customer's display name is its company name; a private one
 * is first + last. Shared between the customer grid's own name column,
 * global search's result rendering in the shell (WP-6c) and the deal
 * workspace — one definition, not two. It lives in src/shared/ because the
 * eager shell needs it: through a module's door it would pull that module's
 * code into the entry chunk (ADR-161 amendment, KAN-636 step 2a). */
export function customerName(c: CustomerRead): string {
  return c.customerType === 'business' ? (c.companyName ?? '') : `${c.firstName ?? ''} ${c.lastName ?? ''}`.trim()
}
