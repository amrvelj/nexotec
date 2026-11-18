/** KAN-215 — the mailbox link's browser side, apart from the screens so a test
 * can replace the navigation away to the provider. */
import type { MailProviderCode } from '../../api/types'

export const MAIL_PROVIDERS: MailProviderCode[] = ['microsoft_365_mail', 'google_workspace_mail']

export const OWN_MAILBOX_KEY = ['mail', 'own-mailbox'] as const
export const businessMailboxKey = (dealershipId: string) => ['mail', 'business-mailbox', dealershipId] as const

/** Leaves the app for the provider's sign-in (or Microsoft's consent screen). */
export const mailNavigation = {
  toProvider(url: string): void {
    window.location.assign(url)
  },
}

/** The query the provider sent the browser back with, as the API takes it. */
export function callbackBody(search: string): {
  state: string
  code?: string
  error?: string
  adminConsent?: string
  tenant?: string
} | null {
  const query = new URLSearchParams(search)
  const state = query.get('state')
  if (!state) return null
  const optional = (name: string) => query.get(name) ?? undefined
  return {
    state,
    code: optional('code'),
    error: optional('error'),
    adminConsent: optional('admin_consent'),
    tenant: optional('tenant'),
  }
}
