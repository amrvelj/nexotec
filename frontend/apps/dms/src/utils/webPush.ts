import { api } from '../api/client'
import type { PushSubscriptionCreate, PushSubscriptionRead } from '../api/types'

/**
 * Web Push in the browser (KAN-216; Dealer Administration FR-A-49): the
 * service worker `/sw.js` (public/sw.js) shows a push's title and opens its
 * link; this module asks the browser for permission, subscribes it with the
 * platform's VAPID key and registers the subscription with the API, which
 * binds it to the current session — a revoked session loses it.
 *
 * The server's id of this browser's subscription is remembered per browser
 * (a convenience for switching push off again); the server never needs it
 * to keep working.
 */

const SERVICE_WORKER_URL = '/sw.js'
const SUBSCRIPTION_ID_KEY = 'nexotec.pushSubscriptionId'

export class PushPermissionDenied extends Error {}

export function pushSupported(): boolean {
  return (
    typeof window !== 'undefined' &&
    typeof navigator !== 'undefined' &&
    'serviceWorker' in navigator &&
    'PushManager' in window &&
    'Notification' in window
  )
}

function rememberedId(): string | null {
  try {
    return window.localStorage.getItem(SUBSCRIPTION_ID_KEY)
  } catch {
    return null
  }
}

function remember(id: string | null): void {
  try {
    if (id) window.localStorage.setItem(SUBSCRIPTION_ID_KEY, id)
    else window.localStorage.removeItem(SUBSCRIPTION_ID_KEY)
  } catch {
    // Storage blocked: switching off again then needs the browser's own setting.
  }
}

/** A VAPID key (base64url) as the Push API takes it. */
export function applicationServerKey(base64url: string): Uint8Array<ArrayBuffer> {
  const padded = (base64url + '='.repeat((4 - (base64url.length % 4)) % 4)).replace(/-/g, '+').replace(/_/g, '/')
  const raw = window.atob(padded)
  const bytes = new Uint8Array(new ArrayBuffer(raw.length))
  for (let i = 0; i < raw.length; i += 1) bytes[i] = raw.charCodeAt(i)
  return bytes
}

async function registration(): Promise<ServiceWorkerRegistration> {
  const existing = await navigator.serviceWorker.getRegistration('/')
  if (existing) return existing
  await navigator.serviceWorker.register(SERVICE_WORKER_URL, { scope: '/' })
  return navigator.serviceWorker.ready
}

function asCreate(subscription: PushSubscription): PushSubscriptionCreate {
  const json = subscription.toJSON()
  return {
    endpoint: json.endpoint ?? subscription.endpoint,
    keys: { p256dh: json.keys?.p256dh ?? '', auth: json.keys?.auth ?? '' },
  }
}

async function register(subscription: PushSubscription): Promise<PushSubscriptionRead> {
  const created = await api.post<PushSubscriptionRead>('/me/push-subscriptions', asCreate(subscription))
  remember(created.id)
  return created
}

/** Whether this browser holds a push subscription and may show notifications. */
export async function deviceSubscribed(): Promise<boolean> {
  if (!pushSupported() || Notification.permission !== 'granted') return false
  const reg = await navigator.serviceWorker.getRegistration('/')
  return Boolean(reg && (await reg.pushManager.getSubscription()))
}

/** Asks for permission (a user gesture calls this), subscribes, registers. */
export async function enablePushOnThisDevice(vapidPublicKey: string): Promise<PushSubscriptionRead> {
  const permission = await Notification.requestPermission()
  if (permission !== 'granted') throw new PushPermissionDenied()
  const reg = await registration()
  const subscription =
    (await reg.pushManager.getSubscription()) ??
    (await reg.pushManager.subscribe({ userVisibleOnly: true, applicationServerKey: applicationServerKey(vapidPublicKey) }))
  return register(subscription)
}

export async function disablePushOnThisDevice(): Promise<void> {
  const reg = pushSupported() ? await navigator.serviceWorker.getRegistration('/') : undefined
  const subscription = reg ? await reg.pushManager.getSubscription() : null
  const id = rememberedId()
  if (id) {
    try {
      await api.delete<void>(`/me/push-subscriptions/${id}`)
    } catch {
      // Already gone (a revoked session took it): nothing to undo server-side.
    }
  }
  remember(null)
  if (subscription) await subscription.unsubscribe()
}

/** A new session on a browser that already pushes: bind the subscription to
 * this session (the previous one's went with it). Silent on any failure. */
export async function rebindPushToThisSession(): Promise<void> {
  try {
    if (!(await deviceSubscribed())) return
    const reg = await navigator.serviceWorker.getRegistration('/')
    const subscription = reg ? await reg.pushManager.getSubscription() : null
    if (subscription) await register(subscription)
  } catch {
    // The inbox is the record; a push that cannot be re-bound is not an error on screen.
  }
}
