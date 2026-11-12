import { Modal, Stack, Text } from '@mantine/core'
import { useQuery } from '@tanstack/react-query'
import { useTranslation } from 'react-i18next'
import { KeyValueRow } from '@nexotec/ui-kit'
import { api } from '../api/client'
import type { IntegrationConnectionRead, IntegrationUsageRead } from '../api/types'
import { formatNumber } from '../utils/format'

/**
 * «Nutzung anzeigen» for one connection: indicative calls and attributed cost
 * of the period (I-2/I-3), never a billing artefact. Opened from the dealer
 * card and the platform grid's row menu alike (KAN-650).
 */
// The 30-day sum of cost_units, a DECIMAL(12, 4) column (app/integration/
// models/call_log.py), arrives as a decimal string; shown at that scale so the
// fourth decimal is never rounded away (KAN-164). A double holds it exactly up
// to about 1e11 units, far beyond any indicative usage figure.
const COST_UNITS_SCALE = 4

function formatCostUnits(value: string | null): string {
  return value === null ? '—' : formatNumber(Number(value), COST_UNITS_SCALE)
}

export function ConnectionUsageModal({ connection, onClose }: { connection: IntegrationConnectionRead | null; onClose: () => void }) {
  const { t } = useTranslation()
  const usageQuery = useQuery({
    queryKey: ['integrations', 'usage', connection?.id],
    queryFn: () => api.get<IntegrationUsageRead>(`/integrations/connections/${connection!.id}/usage`),
    enabled: connection !== null,
  })

  if (!connection) return null

  return (
    <Modal opened onClose={onClose} title={t('integrationsList.usageModal.title', { name: connection.displayName })}>
      <Stack gap="xs">
        {usageQuery.isLoading ? (
          <Text size="sm" c="dimmed">{t('common.loading')}</Text>
        ) : (
          <>
            <KeyValueRow label={t('integrationsList.usageModal.calls')}>
              {formatNumber(usageQuery.data?.callsThisPeriod ?? 0)}
            </KeyValueRow>
            <KeyValueRow label={t('integrationsList.usageModal.cost')}>
              {formatCostUnits(usageQuery.data?.costUnitsThisPeriod ?? null)}
            </KeyValueRow>
            <Text size="xs" c="dimmed">{t('integrationsList.usageModal.indicative')}</Text>
          </>
        )}
      </Stack>
    </Modal>
  )
}
