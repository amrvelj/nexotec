import { Navigate, useLocation } from 'react-router-dom'

// KAN-464 PR-3 — the register lived at /customers until customers became
// contacts; a bookmark or a tab left open keeps working for one release
// (KAN-464 PR-4 removes it with the other leftovers).
export function CustomersMovedToContacts() {
  const location = useLocation()
  return (
    <Navigate
      to={{ pathname: location.pathname.replace(/^\/customers/, '/contacts'), search: location.search, hash: location.hash }}
      replace
    />
  )
}
