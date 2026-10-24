import { useState } from 'react'
import { useMutation, useQueryClient } from '@tanstack/react-query'
import { useTranslation } from 'react-i18next'
import { api } from '../../api/client'
import type { DealershipRead, DealershipUpdate } from '../../api/types'
import { businessRefusalText } from '../../businessOptions'
import { dealershipKey } from './queryKeys'

/**
 * One PATCH of the dealership for one section of the Business screen: the
 * section's changes only, If-Match from the record the form was loaded
 * from, the refusal in the UI language. A success refreshes the record for
 * every section (the version moved).
 */
export function useDealershipPatch(dealership: DealershipRead) {
  const { t } = useTranslation()
  const queryClient = useQueryClient()
  const [error, setError] = useState<string | null>(null)
  const [saved, setSaved] = useState(false)
  const mutation = useMutation({
    mutationFn: (changes: DealershipUpdate) =>
      api.patch<DealershipRead>(`/dealerships/${dealership.id}`, changes, { 'If-Match': String(dealership.version) }),
    onSuccess: (updated) => {
      setError(null)
      setSaved(true)
      queryClient.setQueryData(dealershipKey(dealership.id), updated)
    },
    onError: (err) => {
      setSaved(false)
      setError(businessRefusalText(t, err))
    },
  })
  return { save: mutation.mutate, saving: mutation.isPending, error, clearError: () => setError(null), saved, resetSaved: () => setSaved(false) }
}
