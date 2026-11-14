import { useState } from 'react'
import { Group, Modal, Stack, Table, Text, TextInput } from '@mantine/core'
import { useQuery } from '@tanstack/react-query'
import { useTranslation } from 'react-i18next'
import { api } from '../api/client'
import type { IntegrationConnectionRead, IntegrationUsageRead } from '../api/types'
import { formatNumber, todayLocalIsoDate } from '../utils/format'

/** The default period: the 30 calendar days ending today, as the API's own default. */
const DEFAULT_PERIOD_DAYS = 30

/** The calendar day `days` before `isoDate` (both `YYYY-MM-DD`), as the user's local calendar counts. */
function isoDaysBefore(isoDate: string, days: number): string {
  const [y, m, d] = isoDate.split('-').map(Number)
  return todayLocalIsoDate(new Date(y, m - 1, d - days))
}

/**
 * «Nutzung anzeigen» for one connection (KAN-222, FR-A-23): the requests of a
 * period per provider counter, the counter named verbatim as the provider
 * names it — never translated, never priced (Anto, 2026-11-14, Q1 A: counts
 * only). Indicative: the provider's own count is the one that bills (I-2/I-3).
 * Opened from the dealer card and the platform grid's row menu alike (KAN-650).
 */
export function ConnectionUsageModal({ connection, onClose }: { connection: IntegrationConnectionRead | null; onClose: () => void }) {
  if (!connection) return null
  return <UsageModalBody connection={connection} onClose={onClose} />
}

function UsageModalBody({ connection, onClose }: { connection: IntegrationConnectionRead; onClose: () => void }) {
  const { t } = useTranslation()
  const [to, setTo] = useState(() => todayLocalIsoDate())
  const [from, setFrom] = useState(() => isoDaysBefore(todayLocalIsoDate(), DEFAULT_PERIOD_DAYS - 1))
  const periodValid = from !== '' && to !== '' && from <= to

  const usageQuery = useQuery({
    queryKey: ['integrations', 'usage', connection.id, from, to],
    queryFn: () =>
      api.get<IntegrationUsageRead>(
        `/integrations/connections/${connection.id}/usage?from=${encodeURIComponent(from)}&to=${encodeURIComponent(to)}`
      ),
    enabled: periodValid,
  })

  return (
    <Modal opened onClose={onClose} title={t('integrationsList.usageModal.title', { name: connection.displayName })}>
      <Stack gap="sm">
        <Group grow>
          <TextInput
            type="date"
            label={t('integrationsList.usageModal.from')}
            value={from}
            max={to}
            onChange={(e) => setFrom(e.currentTarget.value)}
          />
          <TextInput
            type="date"
            label={t('integrationsList.usageModal.to')}
            value={to}
            min={from}
            onChange={(e) => setTo(e.currentTarget.value)}
          />
        </Group>
        {!periodValid ? (
          <Text size="sm" c="red" role="alert">{t('integrationsList.usageModal.invalidPeriod')}</Text>
        ) : usageQuery.isLoading ? (
          <Text size="sm" c="dimmed">{t('common.loading')}</Text>
        ) : usageQuery.isError ? (
          <Text size="sm" c="red" role="alert">{t('integrationsList.usageModal.loadError')}</Text>
        ) : (usageQuery.data?.counters ?? []).length === 0 ? (
          <Text size="sm" c="dimmed">{t('integrationsList.usageModal.empty')}</Text>
        ) : (
          <Table>
            <Table.Thead>
              <Table.Tr>
                <Table.Th>{t('integrationsList.usageModal.counter')}</Table.Th>
                <Table.Th style={{ textAlign: 'right' }}>{t('integrationsList.usageModal.count')}</Table.Th>
              </Table.Tr>
            </Table.Thead>
            <Table.Tbody>
              {(usageQuery.data?.counters ?? []).map((row) => (
                <Table.Tr key={row.counter}>
                  {/* The provider's own name for the counter, verbatim. */}
                  <Table.Td><Text size="sm" ff="monospace">{row.counter}</Text></Table.Td>
                  <Table.Td style={{ textAlign: 'right' }}>{formatNumber(row.count)}</Table.Td>
                </Table.Tr>
              ))}
              <Table.Tr>
                <Table.Td><Text size="sm" fw={600}>{t('integrationsList.usageModal.total')}</Text></Table.Td>
                <Table.Td style={{ textAlign: 'right' }}>
                  <Text size="sm" fw={600}>{formatNumber(usageQuery.data?.totalCalls ?? 0)}</Text>
                </Table.Td>
              </Table.Tr>
            </Table.Tbody>
          </Table>
        )}
        <Text size="xs" c="dimmed">{t('integrationsList.usageModal.indicative')}</Text>
      </Stack>
    </Modal>
  )
}
