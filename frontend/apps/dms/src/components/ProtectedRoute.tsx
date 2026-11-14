import type { ReactNode } from 'react'
import { Navigate, useLocation } from 'react-router-dom'
import { Center, Loader } from '@mantine/core'
import { useAuth } from '../auth/AuthContext'
import { LEGAL_ACCEPTANCE_ROUTE, LegalAcceptancePrompt } from './setup/LegalAcceptancePrompt'

export const ACCOUNT_SECURITY_PATH = '/account/security'
// KAN-214 — the manager's first screen while the Betrieb is being set up,
// and what everyone else sees then. Routes of lazy pages (App.tsx).
export const SETUP_CHECKLIST_PATH = '/administration/setup'
export const SETUP_IN_PROGRESS_ROUTE = '/setup-in-progress'

const underPath = (pathname: string, prefix: string) => pathname === prefix || pathname.startsWith(prefix + '/')

export function ProtectedRoute({ children }: { children: ReactNode }) {
  const { user, loading, mfaEnrolmentRequired, activeDealership, isActiveDealershipManager, legalAcceptanceDue } = useAuth()
  const location = useLocation()

  if (loading) {
    return (
      <Center h="100vh">
        <Loader />
      </Center>
    )
  }

  if (!user) {
    return <Navigate to="/login" replace />
  }

  // KAN-196 (D-A-36, FR-AU-11): a session minted without a second factor
  // while the Betrieb — or the platform, for staff — requires one goes to
  // enrolment before any other screen.
  if (mfaEnrolmentRequired && location.pathname !== ACCOUNT_SECURITY_PATH) {
    return <Navigate to={ACCOUNT_SECURITY_PATH} replace />
  }

  const path = location.pathname
  const isStaff = user.accessRoles.includes('platform_admin')

  // KAN-214 (D-A-21): until «Betrieb aktivieren» the manager reaches Dealer
  // Administration and My account, landing on the checklist; everyone else
  // sees «Betrieb wird eingerichtet». Presentation only — the server answers
  // 404 to every module route meanwhile. Staff set the Betrieb up and are
  // never held here.
  if (activeDealership?.status === 'pending_onboarding' && !isStaff) {
    const accountOrInbox = underPath(path, '/account') || underPath(path, '/notifications')
    if (isActiveDealershipManager) {
      if (!underPath(path, '/administration') && !accountOrInbox) return <Navigate to={SETUP_CHECKLIST_PATH} replace />
    } else if (path !== SETUP_IN_PROGRESS_ROUTE && !accountOrInbox) {
      return <Navigate to={SETUP_IN_PROGRESS_ROUTE} replace />
    }
  }

  // KAN-214 (FR-A-39, Q1 A): past the grace for a new ToS / DPA version a
  // manager reaches only the acceptance (the server holds the rest with 403).
  if (legalAcceptanceDue?.held && path !== LEGAL_ACCEPTANCE_ROUTE && !underPath(path, '/account')) {
    return <Navigate to={LEGAL_ACCEPTANCE_ROUTE} replace />
  }

  return (
    <>
      {children}
      <LegalAcceptancePrompt />
    </>
  )
}
