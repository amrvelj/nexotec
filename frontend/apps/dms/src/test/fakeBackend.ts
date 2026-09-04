import { vi } from 'vitest'

// A fetch-level fake of the DMS API. Render tests stub `global.fetch` with
// this rather than mocking `api/client.ts` or any hook, so the real
// request path — `api.get`/`api.put`, the `/v1` base URL, the error
// taxonomy, the debounced preference PUT in `usePersistedPreference` — all
// execute exactly as in the browser. Matching is on pathname; the first
// route whose method and pattern match wins, so a test can shadow a
// default by declaring its own route first.

export interface FakeRequest {
  url: URL
  /** `url.pathname` with the client's `/v1` base prefix stripped — the
   * same string route patterns are tested against. */
  pathname: string
  method: string
  params: URLSearchParams
  body: unknown
}

export type FakeHandler = (req: FakeRequest) => unknown

export interface FakeRoute {
  method?: string
  /** Tested against `url.pathname` (no query string, no `/v1` prefix — the
   * client's base URL already carries that, e.g. `/customers`). */
  match: RegExp
  handler: FakeHandler
}

export interface RecordedCall {
  method: string
  path: string
  pathname: string
  params: URLSearchParams
  body: unknown
}

export interface FakeBackend {
  calls: RecordedCall[]
  /** Calls matching a pathname pattern, newest last. */
  callsTo: (pattern: RegExp, method?: string) => RecordedCall[]
}

const DEFAULT_USER = {
  id: 'user-1',
  dealershipId: 'dealership-1',
  firstName: 'Test',
  lastName: 'Advisor',
  email: 'advisor@example.ch',
  phone: null,
  role: 'Advisor',
  accessRoles: ['sales'],
  isDealerManager: false,
  employmentStatus: 'employed',
  authIdentityId: 'auth-1',
  status: 'active',
  version: 1,
  createdAt: '2026-01-01T00:00:00Z',
  updatedAt: '2026-01-01T00:00:00Z',
}

const DEFAULT_DEALERSHIP = { id: 'dealership-1', legalName: 'Test Motors AG' }

// Infra endpoints every screen hits through its providers (AuthProvider,
// UiPreferencesProvider). Appended after the test's own routes so a test
// can still override any of them.
const INFRA_ROUTES: FakeRoute[] = [
  {
    method: 'GET',
    match: /\/auth\/me$/,
    handler: () => ({ user: DEFAULT_USER, activeDealership: DEFAULT_DEALERSHIP, memberships: [DEFAULT_DEALERSHIP] }),
  },
  { method: 'GET', match: /\/me\/preferences\//, handler: () => ({ payload: {} }) },
  { method: 'PUT', match: /\/me\/preferences\//, handler: () => ({ ok: true }) },
]

const JSON_HEADERS = { 'content-type': 'application/json' }

function toResponse(value: unknown): Response {
  if (value instanceof Response) return value
  if (value && typeof value === 'object' && '__status' in value) {
    const v = value as { __status: number; body?: unknown }
    if (v.__status === 204) return new Response(null, { status: 204 })
    return new Response(JSON.stringify(v.body ?? null), { status: v.__status, headers: JSON_HEADERS })
  }
  return new Response(JSON.stringify(value ?? null), { status: 200, headers: JSON_HEADERS })
}

/** Return this from a handler to send a non-200 (e.g. `status(409, body)`). */
export function status(code: number, body?: unknown) {
  return { __status: code, body }
}

export interface FakeBackendOptions {
  /** Handler for any request that matched no explicit route and no infra
   * route — instead of a 404. Useful for a broad sweep (the i18n route
   * walk) where the point is that every screen renders *something*
   * translated, content / empty / error alike. */
  fallback?: FakeHandler
}

export function installFakeBackend(routes: FakeRoute[], options: FakeBackendOptions = {}): FakeBackend {
  const all = [...routes, ...INFRA_ROUTES]
  const calls: RecordedCall[] = []

  const fetchImpl = async (input: RequestInfo | URL, init?: RequestInit): Promise<Response> => {
    const rawUrl = typeof input === 'string' ? input : input instanceof URL ? input.href : input.url
    const url = new URL(rawUrl, 'http://localhost:8000')
    const method = (init?.method ?? 'GET').toUpperCase()
    let body: unknown
    if (typeof init?.body === 'string') {
      try {
        body = JSON.parse(init.body)
      } catch {
        body = init.body
      }
    }
    // The client's base URL carries the `/v1` prefix; routes are written
    // against the bare resource path (`/customers`, `/me/preferences/ui`).
    const pathname = url.pathname.replace(/^\/v1(?=\/|$)/, '')
    calls.push({ method, path: pathname + url.search, pathname, params: url.searchParams, body })

    const route = all.find((r) => (r.method ?? 'GET').toUpperCase() === method && r.match.test(pathname))
    const req: FakeRequest = { url, pathname, method, params: url.searchParams, body }
    if (!route) {
      if (options.fallback) return toResponse(options.fallback(req))
      return new Response(
        JSON.stringify({ error: { code: 'not_found', message: `fakeBackend: no route for ${method} ${pathname}` } }),
        { status: 404, headers: JSON_HEADERS },
      )
    }
    return toResponse(route.handler(req))
  }

  vi.stubGlobal('fetch', vi.fn(fetchImpl))

  return {
    calls,
    callsTo: (pattern, method) => calls.filter((c) => pattern.test(c.pathname) && (!method || c.method === method.toUpperCase())),
  }
}
