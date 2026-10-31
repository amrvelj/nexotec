import { useState } from 'react'
import { useMutation, useQueryClient } from '@tanstack/react-query'
import { useTranslation } from 'react-i18next'
import { api } from '../../api/client'
import type { LocationRead, LocationUpdate } from '../../api/types'
import { apiRefusalText } from '../../utils/apiRefusal'
import { locationKey, locationsKey } from './locationOptions'

/**
 * One PATCH of a location for one section of its screen (KAN-204): the
 * section's changes only, If-Match from the record the form was loaded from,
 * the refusal in the UI language. A success puts the new record in the cache
 * for every section (its version moved) and refreshes the list.
 */
export function useLocationPatch(dealershipId: string, locationId: string) {
  const { t } = useTranslation()
  const queryClient = useQueryClient()
  const [error, setError] = useState<string | null>(null)
  const [saved, setSaved] = useState(false)
  const mutation = useMutation({
    mutationFn: ({ changes, version }: { changes: LocationUpdate; version: number }) =>
      api.patch<LocationRead>(`/dealerships/${dealershipId}/locations/${locationId}`, changes, { 'If-Match': String(version) }),
    onSuccess: (updated) => {
      setError(null)
      setSaved(true)
      queryClient.setQueryData(locationKey(dealershipId, locationId), updated)
      void queryClient.invalidateQueries({ queryKey: locationsKey(dealershipId), exact: true })
    },
    onError: (err) => {
      setSaved(false)
      setError(apiRefusalText(t, err, 'locations.saveError'))
    },
  })
  return { save: mutation.mutate, saving: mutation.isPending, error, clearError: () => setError(null), saved }
}

/** Empty input → null, else the trimmed string — PATCH semantics for a nullable text field. */
export function orNull(value: string): string | null {
  const trimmed = value.trim()
  return trimmed === '' ? null : trimmed
}

/** "47.4141" → 47.4141, "" → null; anything else stays a string the caller refuses. */
export function coordinate(value: string): number | null | 'invalid' {
  const trimmed = value.trim().replace(',', '.')
  if (trimmed === '') return null
  const parsed = Number(trimmed)
  return Number.isFinite(parsed) ? parsed : 'invalid'
}
