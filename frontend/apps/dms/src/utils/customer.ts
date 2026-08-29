import type { CustomerRead } from '../api/types'

/** A business customer's display name is its company name; a private one
 * is first + last. Shared between the customer grid's own name column and
 * global search's result rendering (WP-6c) — one definition, not two. */
export function customerName(c: CustomerRead): string {
  return c.customerType === 'business' ? (c.companyName ?? '') : `${c.firstName ?? ''} ${c.lastName ?? ''}`.trim()
}
