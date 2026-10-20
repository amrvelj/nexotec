/** My account's query keys and tabs (KAN-201), apart from the components so
 * fast refresh keeps working. */
export const ME_KEY = ['me'] as const
export const CONNECTED_KEY = ['me', 'connected-accounts'] as const

/** The tabs of My account, in order; each is its own route under /account. */
export const ACCOUNT_TABS = ['profile', 'security', 'connected-accounts', 'signature'] as const
export type AccountTab = (typeof ACCOUNT_TABS)[number]

/** A phone number as the API takes it (E.164): the spaces people type go. */
export function phoneForApi(value: string): string | null {
  const compact = value.replace(/\s+/g, '')
  return compact || null
}
