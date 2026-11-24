import type { ContactRead } from '../api/types'

/** A business contact's display name is its company name; a private one
 * is first + last. Shared between the contact grid's own name column,
 * global search's result rendering in the shell (WP-6c) and the deal
 * workspace — one definition, not two. It lives in src/shared/ because the
 * eager shell needs it: through a module's door it would pull that module's
 * code into the entry chunk (ADR-161 amendment, KAN-636 step 2a). */
export function contactName(c: ContactRead): string {
  return c.contactType === 'business' ? (c.companyName ?? '') : `${c.firstName ?? ''} ${c.lastName ?? ''}`.trim()
}
