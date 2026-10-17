import type { ReactNode } from 'react'
import { Navigate, useLocation } from 'react-router-dom'
import { Center, Loader } from '@mantine/core'
import { useAuth } from '../auth/AuthContext'

export const ACCOUNT_SECURITY_PATH = '/account/security'

export function ProtectedRoute({ children }: { children: ReactNode }) {
  const { user, loading, mfaEnrolmentRequired } = useAuth()
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

  return <>{children}</>
}
