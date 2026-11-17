import { useState } from 'react'
import { Alert, Button, Group, Stack, Text } from '@mantine/core'
import { RefreshCw } from 'lucide-react'
import { useTranslation } from 'react-i18next'
import { apiRefusalText } from '../../utils/apiRefusal'

/** The fields Stock names, in the order the banner lists them (KAN-553). */
export const VEHICLE_CHANGED_FIELDS = ['listPrice', 'effectivePrice', 'options', 'exteriorColour', 'odometerKm', 'configurationLabel'] as const

export interface VehicleChangedBannerProps {
  vehicleChangedAt: string | null | undefined
  vehicleChangedFields: string[] | null | undefined
  /**
   * The two actions, each sent through the page's save queue (one after the
   * other, with the version the previous save returned). Absent on a deal
   * that cannot be edited (a sent offer): the banner then only says it.
   */
  onRefresh?: () => Promise<unknown>
  onDismiss?: () => Promise<unknown>
  /** A save in flight or an unsaved price: the actions wait for it. */
  busy?: boolean
}

/**
 * «Fahrzeug im Lager geändert» (KAN-553, ADR-115; Anto's ruling on KAN-33,
 * 2026-11-03): the deal's vehicle snapshot stays frozen (ADR-041); this says
 * that the stock car changed after the deal was priced and which fields, with
 * «Aktualisieren» (re-freeze and re-price) and «Ignorieren» (keep the frozen
 * car). One component for the offer workspace, the sent offer and the
 * pending contract.
 */
export function VehicleChangedBanner({ vehicleChangedAt, vehicleChangedFields, onRefresh, onDismiss, busy = false }: VehicleChangedBannerProps) {
  const { t } = useTranslation()
  const [running, setRunning] = useState<'refresh' | 'dismiss' | null>(null)
  const [error, setError] = useState<string | null>(null)
  if (!vehicleChangedAt) return null

  const known = VEHICLE_CHANGED_FIELDS.filter((field) => vehicleChangedFields?.includes(field))
  const fields = known.map((field) => t(`offerWorkspace.vehicleChanged.fields.${field}`)).join(', ')
  const run = async (which: 'refresh' | 'dismiss', action: () => Promise<unknown>) => {
    setRunning(which)
    setError(null)
    try {
      await action()
    } catch (err) {
      setError(apiRefusalText(t, err, 'offerWorkspace.vehicleChanged.refused'))
    } finally {
      setRunning(null)
    }
  }

  return (
    <Alert color="yellow" icon={<RefreshCw size={18} />} title={t('offerWorkspace.vehicleChanged.title')} data-testid="vehicle-changed-banner">
      <Stack gap="xs">
        <Text size="sm">{fields ? t('offerWorkspace.vehicleChanged.fieldsLine', { fields }) : t('offerWorkspace.vehicleChanged.noFields')}</Text>
        {onRefresh && onDismiss ? (
          <Group gap="xs">
            <Button size="xs" loading={running === 'refresh'} disabled={busy || running !== null} onClick={() => void run('refresh', onRefresh)}>
              {t('offerWorkspace.vehicleChanged.refresh')}
            </Button>
            <Button size="xs" variant="default" loading={running === 'dismiss'} disabled={busy || running !== null} onClick={() => void run('dismiss', onDismiss)}>
              {t('offerWorkspace.vehicleChanged.dismiss')}
            </Button>
          </Group>
        ) : (
          <Text size="sm" c="dimmed">
            {t('offerWorkspace.vehicleChanged.readOnly')}
          </Text>
        )}
        {error && (
          <Text size="sm" c="red" role="alert">
            {error}
          </Text>
        )}
      </Stack>
    </Alert>
  )
}
