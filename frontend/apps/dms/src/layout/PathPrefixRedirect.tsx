import { Navigate, useLocation } from 'react-router-dom'

/**
 * An old path keeps working after its screen moved: the location under `from`
 * opens the same place under `to`, its query and hash kept. KAN-464 PR-3 uses
 * it for /customers → /contacts until KAN-464 PR-5 retires the old names.
 */
export function PathPrefixRedirect({ from, to }: { from: string; to: string }) {
  const location = useLocation()
  const rest = location.pathname.startsWith(from) ? location.pathname.slice(from.length) : ''
  return <Navigate to={{ pathname: `${to}${rest}`, search: location.search, hash: location.hash }} replace />
}
