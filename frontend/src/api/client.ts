import type { ApiErrorBody } from './types'

// Session auth is an httpOnly cookie (issue #8) — this app never reads or
// stores the JWT itself, so there's nothing here but `credentials: 'include'`
// on every request.
const API_BASE_URL = import.meta.env.VITE_API_BASE_URL ?? 'http://localhost:8000/v1'

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
    const errorBody = body as ApiErrorBody | null
    throw new ApiError(
      response.status,
      errorBody?.error?.code ?? 'unknown_error',
      errorBody?.error?.message ?? 'Something went wrong.',
      errorBody?.error?.details ?? null,
    )
  }

  return body as T
}

export const api = {
  get: <T>(path: string) => request<T>(path),
  post: <T>(path: string, body?: unknown, extraHeaders?: HeadersInit) =>
    request<T>(path, { method: 'POST', body: body ? JSON.stringify(body) : undefined, headers: extraHeaders }),
  patch: <T>(path: string, body: unknown, extraHeaders?: HeadersInit) =>
    request<T>(path, { method: 'PATCH', body: JSON.stringify(body), headers: extraHeaders }),
}
