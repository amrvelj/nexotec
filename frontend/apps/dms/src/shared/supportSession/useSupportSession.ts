import { useQuery } from '@tanstack/react-query'
import { api } from '../../api/client'
import type { MySupportSessionRead } from '../../api/types'
import { BACKGROUND_REQUEST_HEADERS } from '../../hooks/useNotifications'

export const MY_SUPPORT_SESSION_KEY = ['me', 'support-session'] as const

/** No Web Push adapter yet (VAPID is Anto's decision): the banner learns of a
 * session by asking — once a minute and whenever the tab regains focus. */
export const SUPPORT_SESSION_POLL_MS = 60_000

/**
 * KAN-221 PR-6 (D-A-05, FR-A-18) — the support session running on the
 * signed-in person: as the user it supports, or as the staff member acting
 * for them (once PR 3 mints that token). A background request: it never
 * counts as activity, so an unattended tab still idles out (FR-AU-19).
 */
export function useSupportSession(enabled: boolean) {
  return useQuery({
    queryKey: MY_SUPPORT_SESSION_KEY,
    queryFn: () => api.get<MySupportSessionRead>('/me/support-session', BACKGROUND_REQUEST_HEADERS),
    enabled,
    refetchInterval: enabled ? SUPPORT_SESSION_POLL_MS : false,
    refetchOnWindowFocus: true,
    retry: false,
  })
}
