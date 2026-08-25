import { createContext, useCallback, useContext, useEffect, useState, type ReactNode } from 'react'
import { api } from '../api/client'
import type { DealershipMembershipSummary, LoginResponse, UserRead } from '../api/types'

interface AuthContextValue {
  user: UserRead | null
  activeDealership: DealershipMembershipSummary | null
  memberships: DealershipMembershipSummary[]
  loading: boolean
  logout: () => Promise<void>
  /** Re-issues the session against a different dealership from
   * `memberships` (WP-3 PR-3) — the sidebar switcher's only action. */
  switchDealership: (dealershipId: string) => Promise<void>
}

const AuthContext = createContext<AuthContextValue | null>(null)

export function AuthProvider({ children }: { children: ReactNode }) {
  const [user, setUser] = useState<UserRead | null>(null)
  const [activeDealership, setActiveDealership] = useState<DealershipMembershipSummary | null>(null)
  const [memberships, setMemberships] = useState<DealershipMembershipSummary[]>([])
  const [loading, setLoading] = useState(true)

  const applySession = useCallback((res: LoginResponse) => {
    setUser(res.user)
    setActiveDealership(res.activeDealership)
    setMemberships(res.memberships)
  }, [])

  const clearSession = useCallback(() => {
    setUser(null)
    setActiveDealership(null)
    setMemberships([])
  }, [])

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
      const res = await api.post<LoginResponse>('/auth/switch-dealership', { dealershipId })
      applySession(res)
    },
    [applySession]
  )

  return (
    <AuthContext.Provider
      value={{ user, activeDealership, memberships, loading, logout, switchDealership }}
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
