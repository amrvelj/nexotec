import { useCallback, useState } from 'react'
import { useMutation, useQueryClient } from '@tanstack/react-query'
import { useTranslation } from 'react-i18next'
import { useNavigate } from 'react-router-dom'
import { ExternalLink, Power, PowerOff } from 'lucide-react'
import type { RowMenuGroups } from '@nexotec/ui-kit'
import { api } from '../../api/client'
import type { LocationRead } from '../../api/types'
import { useCanWrite } from '../../auth/useCanWrite'
import { apiRefusalText } from '../../utils/apiRefusal'
import { locationKey, locationsKey } from './locationOptions'

/**
 * ADR-061 — the one definition of what can be done to a location: the list's
 * row menu and the detail screen's overflow both render from it. Open;
 * deactivate (never delete — A-RULE-1) or activate again. The last active
 * location's deactivation is shown disabled with its reason (FR-A-03); a
 * role without the write sees the open entry only.
 */
export function useLocationRowMenu(dealershipId: string, activeCount: number) {
  const { t } = useTranslation()
  const navigate = useNavigate()
  const queryClient = useQueryClient()
  const canWrite = useCanWrite('locations')
  const [pending, setPending] = useState<LocationRead | null>(null)
  const [error, setError] = useState<string | null>(null)

  const toggle = useMutation({
    mutationFn: ({ location, isActive }: { location: LocationRead; isActive: boolean }) =>
      api.patch<LocationRead>(`/dealerships/${dealershipId}/locations/${location.id}`, { isActive }, { 'If-Match': String(location.version) }),
    onSuccess: (updated) => {
      setError(null)
      setPending(null)
      queryClient.setQueryData(locationKey(dealershipId, updated.id), updated)
      void queryClient.invalidateQueries({ queryKey: locationsKey(dealershipId), exact: true })
    },
    onError: (err) => setError(apiRefusalText(t, err, 'locations.saveError')),
  })

  // The two actions the menu offers, named, so the detail screen's primary
  // calls the very same functions as the entries below (ADR-061; KAN-512's
  // guard follows a `use…RowMenu` hook's returned actions).
  const requestDeactivation = useCallback((location: LocationRead) => {
    setError(null)
    setPending(location)
  }, [])
  const activate = useCallback((location: LocationRead) => toggle.mutate({ location, isActive: true }), [toggle])

  const groups = useCallback(
    (location: LocationRead): RowMenuGroups => {
      const out: RowMenuGroups = {
        navigate: [
          { label: t('locations.actions.open'), icon: <ExternalLink size={16} />, onClick: () => navigate(`/administration/locations/${location.id}`) },
        ],
      }
      if (!canWrite) return out
      if (location.isActive) {
        const last = activeCount <= 1
        out.edit = [
          {
            label: t('locations.actions.deactivate'),
            icon: <PowerOff size={16} />,
            onClick: () => requestDeactivation(location),
            disabled: last,
            disabledReason: last ? t('locations.actions.lastActive') : undefined,
          },
        ]
      } else {
        out.edit = [
          {
            label: t('locations.actions.activate'),
            icon: <Power size={16} />,
            onClick: () => activate(location),
          },
        ]
      }
      return out
    },
    [t, navigate, canWrite, activeCount, requestDeactivation, activate],
  )

  return {
    groups,
    /** Asks for the deactivation's confirmation (the dialog below opens on `pending`). */
    requestDeactivation,
    activate,
    /** The location waiting for the deactivation's confirmation. */
    pending,
    cancel: () => {
      setPending(null)
      setError(null)
    },
    confirm: () => pending && toggle.mutate({ location: pending, isActive: false }),
    saving: toggle.isPending,
    error,
    clearError: () => setError(null),
  }
}
