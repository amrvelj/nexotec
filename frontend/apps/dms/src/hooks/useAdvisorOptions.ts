import { useQuery } from '@tanstack/react-query'
import { api } from '../api/client'
import type { CustomerAdvisorOptionList } from '../api/types'

/** `{ value, label }` for the customer record's advisor `Select` (KAN-50 /
 * D-24). Backed by `GET /v1/customers/advisor-options` — active users of
 * the ACTING dealership, the same list the server validates an assignment
 * against. Cached for the session; the roster changes rarely. */
export function useAdvisorOptions(): {
  options: { value: string; label: string }[]
  isLoading: boolean
  isError: boolean
} {
  const query = useQuery({
    queryKey: ['customers', 'advisor-options'],
    queryFn: () => api.get<CustomerAdvisorOptionList>('/customers/advisor-options'),
    staleTime: 10 * 60 * 1000,
    gcTime: 30 * 60 * 1000,
  })

  return {
    options: (query.data?.items ?? []).map((row) => ({ value: row.id, label: row.label })),
    isLoading: query.isLoading,
    isError: query.isError,
  }
}
