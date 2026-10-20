import i18n from '../i18n'
import type { ApiErrorBody } from './types'

// Session auth is an httpOnly cookie (issue #8) — this app never reads or
// stores the JWT itself, so there's nothing here but `credentials: 'include'`
// on every request. The login itself is an ordinary POST through this client
// (KAN-196, `/auth/login`): the cookie comes back on the response.
export const API_BASE_URL = import.meta.env.VITE_API_BASE_URL ?? 'http://localhost:8000/v1'

export class ApiError extends Error {
  code: string
  status: number
  details: Record<string, unknown> | null

  constructor(status: number, code: string, message: string, details: Record<string, unknown> | null) {
    super(message)
    this.status = status
    this.code = code
    this.details = details
  }
}

// KAN-271: a response without the API's error body (a gateway's HTML 502/
// 503/504, an empty or non-JSON body) still reaches the screen as an
// `ApiError`, and most screens show `err.message` as it is — so the fallback
// is a translated message in the active UI language, never a literal.
function toApiError(status: number, errorBody: ApiErrorBody | null): ApiError {
  return new ApiError(
    status,
    errorBody?.error?.code ?? 'unknown_error',
    errorBody?.error?.message ?? i18n.t('common.requestFailed'),
    errorBody?.error?.details ?? null,
  )
}

// KAN-237 — a 401 from any call means the server no longer accepts this
// browser's session (cookie expired, user deactivated, …). The client
// session ends with it: AuthProvider subscribes here and clears its user,
// and ProtectedRoute then routes to the login screen. Clearing a session
// that is already gone changes nothing, so the 401 that `/auth/me` answers
// a signed-out visitor (on /login or a reset/invitation screen) routes nowhere and
// cannot loop. There is no token-refresh call to exempt: the session is one
// httpOnly cookie, renewed only by signing in again.
type UnauthorizedListener = () => void
const unauthorizedListeners = new Set<UnauthorizedListener>()

/** Subscribes to "the server answered 401"; returns the unsubscribe. */
export function onUnauthorized(listener: UnauthorizedListener): () => void {
  unauthorizedListeners.add(listener)
  return () => {
    unauthorizedListeners.delete(listener)
  }
}

function reportUnauthorized(status: number) {
  if (status !== 401) return
  for (const listener of [...unauthorizedListeners]) listener()
}

async function request<T>(path: string, init: RequestInit = {}): Promise<T> {
  const response = await fetch(`${API_BASE_URL}${path}`, {
    ...init,
    credentials: 'include',
    headers: {
      ...(init.body ? { 'Content-Type': 'application/json' } : {}),
      ...init.headers,
    },
  })

  if (response.status === 204) {
    return undefined as T
  }

  const body = await response.json().catch(() => null)

  if (!response.ok) {
    reportUnauthorized(response.status)
    throw toApiError(response.status, body as ApiErrorBody | null)
  }

  return body as T
}

/** WP-8 PR-7 — the one endpoint that answers with `application/pdf`
 * rather than JSON (`GET /sales/documents/{id}/pdf`); `request()` above
 * always calls `.json()`, so this is a small, separate path rather than a
 * branch inside it. Returns a `blob:` URL the caller must revoke with
 * `URL.revokeObjectURL` once done (`DocumentPreview`'s own `src` prop).
 */
async function getBlobUrl(path: string): Promise<string> {
  const response = await fetch(`${API_BASE_URL}${path}`, { credentials: 'include' })
  if (!response.ok) {
    reportUnauthorized(response.status)
    throw toApiError(response.status, (await response.json().catch(() => null)) as ApiErrorBody | null)
  }
  const blob = await response.blob()
  return URL.createObjectURL(blob)
}

/** KAN-200 — the one upload today (a user's signature image): the body is
 * the file itself with its own Content-Type, never JSON, never base64. */
async function putBinary<T>(path: string, body: Blob, contentType: string, extraHeaders?: HeadersInit): Promise<T> {
  const response = await fetch(`${API_BASE_URL}${path}`, {
    method: 'PUT',
    body,
    credentials: 'include',
    headers: { 'Content-Type': contentType, ...extraHeaders },
  })
  const json = await response.json().catch(() => null)
  if (!response.ok) {
    reportUnauthorized(response.status)
    throw toApiError(response.status, json as ApiErrorBody | null)
  }
  return json as T
}

export const api = {
  get: <T>(path: string) => request<T>(path),
  getBlobUrl,
  putBinary,
  post: <T>(path: string, body?: unknown, extraHeaders?: HeadersInit) =>
    request<T>(path, { method: 'POST', body: body ? JSON.stringify(body) : undefined, headers: extraHeaders }),
  patch: <T>(path: string, body: unknown, extraHeaders?: HeadersInit) =>
    request<T>(path, { method: 'PATCH', body: JSON.stringify(body), headers: extraHeaders }),
  put: <T>(path: string, body: unknown, extraHeaders?: HeadersInit) =>
    request<T>(path, { method: 'PUT', body: JSON.stringify(body), headers: extraHeaders }),
  delete: <T>(path: string, extraHeaders?: HeadersInit) => request<T>(path, { method: 'DELETE', headers: extraHeaders }),
}
