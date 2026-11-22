/** KAN-226 PR-4 — the Business Profile link's browser side, apart from the
 * screens so a test can replace the navigation away to Google. */

export const businessProfileKey = (dealershipId: string) => ['business-profile', dealershipId] as const

/** Leaves the app for Google's sign-in. */
export const businessProfileNavigation = {
  toGoogle(url: string): void {
    window.location.assign(url)
  },
}

/** The query Google sent the browser back with, as the API takes it. */
export function businessProfileCallbackBody(search: string): { state: string; code?: string; error?: string } | null {
  const query = new URLSearchParams(search)
  const state = query.get('state')
  if (!state) return null
  return { state, code: query.get('code') ?? undefined, error: query.get('error') ?? undefined }
}
