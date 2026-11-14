import { useEffect, useState } from 'react'
import { Select, Stack, Text } from '@mantine/core'
import { FormDialog } from '@nexotec/ui-kit'
import { useQuery } from '@tanstack/react-query'
import { useTranslation } from 'react-i18next'
import { api } from '../api/client'
import { useAuth } from '../auth/AuthContext'
import { useFieldHint } from '../hooks/useFieldHint'
import type { IntegrationConnectionRead, LocationList } from '../api/types'
import { apiRefusalText } from '../utils/apiRefusal'
import { locationsKey } from './locations/locationOptions'

/** The Select's value for «Ganzer Betrieb» — no location (`locationId: null`). */
const WHOLE_BUSINESS = '__whole_business__'

/**
 * «Standort zuordnen» (KAN-222, FR-A-21): binds a connection whose accounts
 * are per site (an AutoScout24 branch number) to one of the Betrieb's own
 * locations, or back to the whole Betrieb. A PATCH carrying `locationId`
 * alone, with If-Match. Offers the active locations, plus the bound one when
 * it has since been deactivated.
 */
export function BindLocationModal({
  connection,
  onClose,
  onBound,
}: {
  connection: IntegrationConnectionRead | null
  onClose: () => void
  onBound: () => void
}) {
  const { t } = useTranslation()
  const hint = useFieldHint()
  const { activeDealership } = useAuth()
  const dealershipId = activeDealership?.id ?? ''
  const [value, setValue] = useState<string>(WHOLE_BUSINESS)
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    setValue(connection?.locationId ?? WHOLE_BUSINESS)
    setError(null)
  }, [connection])

  const locations = useQuery({
    queryKey: locationsKey(dealershipId),
    queryFn: () => api.get<LocationList>(`/dealerships/${dealershipId}/locations`),
    enabled: connection !== null && dealershipId !== '',
  })

  if (!connection) return null

  const options = [
    { value: WHOLE_BUSINESS, label: t('integrationsList.locationWholeBusiness') },
    ...(locations.data?.items ?? [])
      .filter((l) => l.isActive || l.id === connection.locationId)
      .map((l) => ({ value: l.id, label: l.name })),
  ]

  const save = async () => {
    setSaving(true)
    setError(null)
    try {
      await api.patch(
        `/integrations/connections/${connection.id}`,
        { locationId: value === WHOLE_BUSINESS ? null : value },
        { 'If-Match': String(connection.version) }
      )
      onBound()
      onClose()
    } catch (err) {
      setError(apiRefusalText(t, err, 'integrationsList.locationModal.error'))
    } finally {
      setSaving(false)
    }
  }

  return (
    <FormDialog
      opened
      onClose={onClose}
      title={t('integrationsList.locationModal.title', { name: connection.displayName })}
      onSubmit={save}
      submitLabel={t('common.save')}
      cancelLabel={t('common.cancel')}
      submitting={saving}
    >
      <Stack gap="sm">
        {error && <Text size="sm" c="red" role="alert">{error}</Text>}
        <Select
          {...hint(t('integrationsList.locationModal.field'), t('integrationsList.locationModal.fieldHint'))}
          data={options}
          value={value}
          allowDeselect={false}
          onChange={(v) => setValue(v ?? WHOLE_BUSINESS)}
        />
      </Stack>
    </FormDialog>
  )
}
