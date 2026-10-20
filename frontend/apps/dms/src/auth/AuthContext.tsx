import { createContext, useCallback, useContext, useEffect, useState, type ReactNode } from 'react'
import { api, ApiError, onUnauthorized } from '../api/client'
import type { DealershipMembershipSummary, LoginResponse, LoginStepResponse } from '../api/types'

/** The signed-in user as the login response carries it (KAN-200: the
 * administration screen's fields are `UserRead`'s, never the session's). */
export type SessionUser = LoginResponse['user']

interface AuthContextValue {
  user: SessionUser | null
  activeDealership: DealershipMembershipSummary | null
  memberships: DealershipMembershipSummary[]
  /** The active dealership's licensed modules (KAN-198, D-A-04): what the
   * shell shows and `RequireEntitlement` honours. Presentation only — the
   * API answers 404 for an unlicensed module whatever the SPA renders. */
  entitlements: string[]
  /** KAN-196 (D-A-36): this session was minted without a second factor
   * while one is required — `ProtectedRoute` sends the user to enrolment. */
  mfaEnrolmentRequired: boolean
  loading: boolean
  /** KAN-196: the first step of a sign-in. Resolves with either the session
   * (already applied here) or the MFA step the caller has to complete. */
  login: (email: string, password: string) => Promise<LoginStepResponse>
  /** Applies a session another call minted (the MFA step, an accepted
   * invitation) — the same thing `login` does on its own success. */
  completeLogin: (session: LoginResponse) => void
  /** Re-reads the session from the server (after enrolling a factor, say). */
  refresh: () => Promise<void>
  logout: () => Promise<void>
  /** Re-issues the session against a different dealership from
   * `memberships` (WP-3 PR-3) — the sidebar switcher's only action.
   * Rejects when the server refuses the switch; the active dealership is
   * then unchanged, and the caller says why (KAN-237). */
  switchDealership: (dealershipId: string) => Promise<void>
}

const AuthContext = createContext<AuthContextValue | null>(null)

export function AuthProvider({ children }: { children: ReactNode }) {
  const [user, setUser] = useState<SessionUser | null>(null)
  const [activeDealership, setActiveDealership] = useState<DealershipMembershipSummary | null>(null)
  const [memberships, setMemberships] = useState<DealershipMembershipSummary[]>([])
  const [entitlements, setEntitlements] = useState<string[]>([])
  const [mfaEnrolmentRequired, setMfaEnrolmentRequired] = useState(false)
  const [loading, setLoading] = useState(true)

  const applySession = useCallback((res: LoginResponse) => {
    setUser(res.user)
    setActiveDealership(res.activeDealership)
    setMemberships(res.memberships)
    setEntitlements(res.entitlements ?? [])
    setMfaEnrolmentRequired(res.mfaEnrolmentRequired ?? false)
  }, [])

  const clearSession = useCallback(() => {
    setUser(null)
    setActiveDealership(null)
    setMemberships([])
    setEntitlements([])
    setMfaEnrolmentRequired(false)
  }, [])

  // KAN-237 — a 401 from any API call ends the client session; with no
  // user, ProtectedRoute routes to the login screen. See api/client.ts for
  // why this cannot loop on /login or the reset/invitation screens.
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

  const login = useCallback(
    async (email: string, password: string) => {
      const step = await api.post<LoginStepResponse>('/auth/login', { email, password })
      if (step.session) applySession(step.session)
      return step
    },
    [applySession]
  )

  const refresh = useCallback(async () => {
    applySession(await api.get<LoginResponse>('/auth/me'))
  }, [applySession])

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
      value={{
        user,
        activeDealership,
        memberships,
        entitlements,
        mfaEnrolmentRequired,
        loading,
        login,
        completeLogin: applySession,
        refresh,
        logout,
        switchDealership,
      }}
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
