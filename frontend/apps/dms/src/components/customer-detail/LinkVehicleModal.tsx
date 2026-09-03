import { useState } from 'react'
import { useDebouncedValue } from '@mantine/hooks'
import { useQuery } from '@tanstack/react-query'
import { Button, Group, Modal, Select, Stack, Text, TextInput } from '@mantine/core'
import { useTranslation } from 'react-i18next'
import { api, ApiError } from '../../api/client'
import { translatedVehiclePartyRoleLabel } from '../../customerOptions'
import type { VehicleMdmRead, VehiclePartyRole, VehicleSearchResult } from '../../api/types'

export interface LinkVehicleModalProps {
  opened: boolean
  onClose: () => void
  onLinked: () => void
  customerId: string
}

/**
 * KAN-14 / FR-22 / FR-10 — the "link vehicle" overflow action the
 * Customer detail screen's `DetailHeader` was missing entirely.
 * `POST /customers/{id}/vehicles` already existed (full CRUD, WP-3) with
 * no frontend caller anywhere — this is the first one. Vehicle search
 * reuses the exact one-search-box endpoint `VehiclesListPage.tsx` itself
 * calls (`GET /vehicle-mdm/search`), never a second lookup mechanism.
 */
export function LinkVehicleModal({ opened, onClose, onLinked, customerId }: LinkVehicleModalProps) {
  const { t } = useTranslation()
  const [query, setQuery] = useState('')
  const [debouncedQuery] = useDebouncedValue(query, 250)
  const [selectedVehicle, setSelectedVehicle] = useState<VehicleMdmRead | null>(null)
  const [role, setRole] = useState<VehiclePartyRole>('owner')
  const [submitting, setSubmitting] = useState(false)
  const [error, setError] = useState<string | null>(null)

  const searchQuery = useQuery({
    queryKey: ['vehicle-search-for-link', debouncedQuery],
    queryFn: () => api.get<VehicleSearchResult>(`/vehicle-mdm/search?q=${encodeURIComponent(debouncedQuery)}`),
    enabled: opened && debouncedQuery.length > 0,
  })
  const candidates = searchQuery.data?.filtered.items ?? []

  const reset = () => {
    setQuery('')
    setSelectedVehicle(null)
    setRole('owner')
    setError(null)
  }

  const submit = async () => {
    if (!selectedVehicle) return
    setSubmitting(true)
    setError(null)
    try {
      await api.post(`/customers/${customerId}/vehicles`, { vehicleId: selectedVehicle.id, role })
      reset()
      onLinked()
      onClose()
    } catch (err) {
      setError(err instanceof ApiError ? err.message : t('customerDetail.linkVehicle.error'))
    } finally {
      setSubmitting(false)
    }
  }

  return (
    <Modal
      opened={opened}
      onClose={() => {
        reset()
        onClose()
      }}
      title={t('customerDetail.linkVehicle.title')}
    >
      <Stack gap="sm">
        {error && <Text size="sm" c="red">{error}</Text>}
        {selectedVehicle ? (
          <Group justify="space-between">
            <div>
              <Text size="sm" fw={600}>{selectedVehicle.vehicleNumber}</Text>
              <Text size="xs" c="dimmed" ff="monospace">{selectedVehicle.vin}</Text>
            </div>
            <Button variant="subtle" size="xs" onClick={() => setSelectedVehicle(null)}>
              {t('common.remove')}
            </Button>
          </Group>
        ) : (
          <>
            <TextInput
              label={t('customerDetail.linkVehicle.search')}
              value={query}
              onChange={(e) => setQuery(e.currentTarget.value)}
              placeholder={t('customerDetail.linkVehicle.searchPlaceholder')}
              data-autofocus
            />
            {candidates.length > 0 && (
              <Stack gap={2}>
                {candidates.map((v) => (
                  <Button key={v.id} variant="default" size="xs" onClick={() => setSelectedVehicle(v)} justify="space-between" fullWidth>
                    {v.vehicleNumber} — {v.vin}
                  </Button>
                ))}
              </Stack>
            )}
          </>
        )}

        <Select
          label={t('customerDetail.linkVehicle.role')}
          data={(['owner', 'keeper', 'driver'] as VehiclePartyRole[]).map((r) => ({
            value: r,
            label: translatedVehiclePartyRoleLabel(t, r),
          }))}
          value={role}
          onChange={(v) => setRole((v as VehiclePartyRole) ?? 'owner')}
        />

        <Group justify="flex-end">
          <Button variant="default" onClick={onClose}>{t('common.cancel')}</Button>
          <Button onClick={() => void submit()} loading={submitting} disabled={!selectedVehicle}>
            {t('customerDetail.linkVehicle.confirm')}
          </Button>
        </Group>
      </Stack>
    </Modal>
  )
}
