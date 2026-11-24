import { useQuery } from '@tanstack/react-query'
import { api } from '../api/client'
import type { ContactAdvisorOptionList } from '../api/types'

/** `{ value, label }` for the contact record's advisor `Select` (KAN-50 /
 * D-24). Backed by `GET /v1/contacts/advisor-options` — the ACTING
 * dealership's active users and active members (KAN-410), the same rule the
 * server validates an assignment against. Cached for the session; the roster changes rarely. */
export function useAdvisorOptions(): {
  options: { value: string; label: string }[]
  isLoading: boolean
  isError: boolean
} {
  const query = useQuery({
    queryKey: ['contacts', 'advisor-options'],
    queryFn: () => api.get<ContactAdvisorOptionList>('/contacts/advisor-options'),
    staleTime: 10 * 60 * 1000,
    gcTime: 30 * 60 * 1000,
  })

  return {
    options: (query.data?.items ?? []).map((row) => ({ value: row.id, label: row.label })),
    isLoading: query.isLoading,
    isError: query.isError,
  }
}
