import { createContext, useCallback, useContext, useEffect, useState, type ReactNode } from 'react'
import { api, ApiError, onUnauthorized } from '../api/client'
import type { DealershipMembershipSummary, LoginResponse, UserRead } from '../api/types'

interface AuthContextValue {
  user: UserRead | null
  activeDealership: DealershipMembershipSummary | null
  memberships: DealershipMembershipSummary[]
  /** The active dealership's licensed modules (KAN-198, D-A-04): what the
   * shell shows and `RequireEntitlement` honours. Presentation only — the
   * API answers 404 for an unlicensed module whatever the SPA renders. */
  entitlements: string[]
  loading: boolean
  logout: () => Promise<void>
  /** Re-issues the session against a different dealership from
   * `memberships` (WP-3 PR-3) — the sidebar switcher's only action.
   * Rejects when the server refuses the switch; the active dealership is
   * then unchanged, and the caller says why (KAN-237). */
  switchDealership: (dealershipId: string) => Promise<void>
}

const AuthContext = createContext<AuthContextValue | null>(null)

export function AuthProvider({ children }: { children: ReactNode }) {
  const [user, setUser] = useState<UserRead | null>(null)
  const [activeDealership, setActiveDealership] = useState<DealershipMembershipSummary | null>(null)
  const [memberships, setMemberships] = useState<DealershipMembershipSummary[]>([])
  const [entitlements, setEntitlements] = useState<string[]>([])
  const [loading, setLoading] = useState(true)

  const applySession = useCallback((res: LoginResponse) => {
    setUser(res.user)
    setActiveDealership(res.activeDealership)
    setMemberships(res.memberships)
    setEntitlements(res.entitlements ?? [])
  }, [])

  const clearSession = useCallback(() => {
    setUser(null)
    setActiveDealership(null)
    setMemberships([])
    setEntitlements([])
  }, [])

  // KAN-237 — a 401 from any API call ends the client session; with no
  // user, ProtectedRoute routes to the login screen. See api/client.ts for
  // why this cannot loop on /login or /sign-in-error.
  useEffect(() => onUnauthorized(clearSession), [clearSession])

  useEffect(() => {
    // Restores "logged in as X" from the still-valid httpOnly cookie after
    // a page reload — see GET /v1/auth/me's docstring for why this exists.
    api
      .get<LoginResponse>('/auth/me')
      .then(applySession)
      .catch(clearSession)
      .finally(() => setLoading(false))
  }, [applySession, clearSession])

  const logout = useCallback(async () => {
    try {
      await api.post('/auth/logout')
    } finally {
      clearSession()
    }
  }, [clearSession])

  const switchDealership = useCallback(
    async (dealershipId: string) => {
      try {
        const res = await api.post<LoginResponse>('/auth/switch-dealership', { dealershipId })
        applySession(res)
      } catch (err) {
        // A 403 is a membership revoked since the switcher was filled:
        // re-read the list (GET /auth/me reads it from the database since
        // KAN-237) so the refused dealership is no longer offered. The
        // session itself — and with it the active dealership — is
        // unchanged.
        if (err instanceof ApiError && err.status === 403) {
          void api.get<LoginResponse>('/auth/me').then(applySession, () => undefined)
        }
        throw err
      }
    },
    [applySession]
  )

  return (
    <AuthContext.Provider
      value={{ user, activeDealership, memberships, entitlements, loading, logout, switchDealership }}
    >
      {children}
    </AuthContext.Provider>
  )
}

export function useAuth(): AuthContextValue {
  const ctx = useContext(AuthContext)
  if (!ctx) throw new Error('useAuth must be used within an AuthProvider')
  return ctx
}
