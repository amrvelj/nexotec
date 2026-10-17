import { Navigate, Outlet } from 'react-router-dom'
import { useAuth } from '../auth/AuthContext'

/**
 * Layout route for a module's screens (KAN-198, D-A-04): a dealership not
 * licensed for `module` is sent to the dashboard instead of a screen whose
 * every request the API would answer with 404. Presentation only — the 404 is
 * the boundary; this only keeps the SPA from rendering an empty module.
 */
export function RequireEntitlement({ module }: { module: string }) {
  const { entitlements, loading } = useAuth()
  // The session is still being restored: deciding now would send every
  // deep link to the dashboard before the entitlements have arrived.
  if (loading) {
    return null
  }
  if (!entitlements.includes(module)) {
    return <Navigate to="/" replace />
  }
  return <Outlet />
}
