import { useQuery } from '@tanstack/react-query'
import type { TFunction } from 'i18next'
import { api } from '../api/client'
import type { NotificationRead, UnreadCountRead } from '../api/types'

/** Staff notifications (KAN-216; D-A-40, FR-A-47…50): query keys and the
 * one way an inbox entry becomes text in the UI language. */
export const NOTIFICATIONS_KEY = ['me', 'notifications'] as const
export const UNREAD_COUNT_KEY = ['me', 'notifications', 'unread-count'] as const
export const NOTIFICATION_PREFERENCES_KEY = ['me', 'notification-preferences'] as const
export const PUSH_CONFIG_KEY = ['me', 'push', 'config'] as const

/** The inbox is the record; the bell asks for its count once a minute. */
const UNREAD_POLL_MS = 60_000

/** A request the app makes on its own, not because the user did something:
 * the session guard checks it but does not count it as activity, so an
 * unattended tab still reaches its idle end (FR-AU-19; `session_guard.py`). */
export const BACKGROUND_REQUEST_HEADERS = { 'X-DMS-Background': 'poll' } as const

export function useUnreadNotificationCount(enabled: boolean) {
  return useQuery({
    queryKey: UNREAD_COUNT_KEY,
    queryFn: () => api.get<UnreadCountRead>('/me/notifications/unread-count', BACKGROUND_REQUEST_HEADERS),
    enabled,
    refetchInterval: enabled ? UNREAD_POLL_MS : false,
  })
}

/** The parameters a body interpolates are closed codes or numbers; a
 * recovery action is a code the bundle names (notifications.recoveryActions). */
function interpolation(t: TFunction, notification: NotificationRead): Record<string, string | number | boolean> {
  const params: Record<string, string | number | boolean> = { ...notification.params }
  if (typeof params.action === 'string') params.action = t(`notifications.recoveryActions.${params.action}`)
  return params
}

export function notificationTitle(t: TFunction, notification: NotificationRead): string {
  return t(notification.titleKey, interpolation(t, notification))
}

export function notificationBody(t: TFunction, notification: NotificationRead): string {
  return t(notification.bodyKey, interpolation(t, notification))
}
