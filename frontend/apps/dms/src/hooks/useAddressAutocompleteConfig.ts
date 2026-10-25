import { useQuery } from '@tanstack/react-query'
import { api } from '../api/client'
import type { AddressAutocompleteConfigRead } from '../api/types'
import { useAuth } from '../auth/AuthContext'

/** The query key the Business screen invalidates when the switch is saved. */
export const addressAutocompleteConfigKey = (dealershipId: string) => ['dealership', dealershipId, 'address-autocomplete'] as const

/**
 * KAN-37 (ADR-101): whether the active Betrieb's address forms offer Google
 * Places Autocomplete, and the browser key when they do. Off until the
 * answer arrives and off on any failure — a form never waits for it, the
 * search field simply appears once the read lands. One cache entry per
 * Betrieb, shared by every address form on screen.
 */
export function useAddressAutocompleteConfig(): { enabled: boolean; browserKey: string | null } {
  const { activeDealership } = useAuth()
  const dealershipId = activeDealership?.id ?? ''
  const query = useQuery({
    queryKey: addressAutocompleteConfigKey(dealershipId),
    queryFn: () => api.get<AddressAutocompleteConfigRead>(`/dealerships/${dealershipId}/address-autocomplete`),
    enabled: dealershipId !== '',
    staleTime: 5 * 60 * 1000,
    retry: false,
  })
  const data = query.data
  if (data?.enabled && data.browserKey) return { enabled: true, browserKey: data.browserKey }
  return { enabled: false, browserKey: null }
}
