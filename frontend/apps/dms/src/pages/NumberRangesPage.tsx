import { useState } from 'react'
import { Alert, Button, Group, Paper, Skeleton, Stack, Table, Text, Title } from '@mantine/core'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import { useTranslation } from 'react-i18next'
import { Badge, useSetBreadcrumb } from '@nexotec/ui-kit'
import { api } from '../api/client'
import type { NumberRangeList, NumberRangeRead } from '../api/types'
import { useAuth } from '../auth/AuthContext'
import { useCanWrite } from '../auth/useCanWrite'
import { NumberRangeDialog, type NumberRangeChange } from '../components/numberRanges/NumberRangeDialog'
import { toSwissLocale, type SupportedLanguage } from '../i18n'
import { apiRefusalText } from '../utils/apiRefusal'
import { formatDateTime } from '../utils/format'

const numberRangesKey = (dealershipId: string) => ['number-ranges', dealershipId]

/**
 * Dealer Administration › Number ranges (KAN-207; PRD "Number ranges", D-A-17,
 * A-RULE-3; A-RULE-0: the one place a Betrieb's numbering is configured).
 * One row per document type of the modules the Betrieb holds: the pattern,
 * the next number as an example and the current value, read-only. The
 * freeze is stated before the first number, not after (A-RULE-3): an open
 * range says it freezes with its first number; a frozen one says only
 * Nexotec support can change it. Any role reads; the manager edits an open
 * range, platform staff a frozen one, with a reason.
 *
 * KAN-214 (D-A-21): the manager confirms the ranges once («Nummernkreise
 * bestätigen») — a setup-checklist item. Confirming freezes nothing; the
 * ranges freeze at «Betrieb aktivieren».
 */
export function NumberRangesPage() {
  const { t, i18n } = useTranslation()
  const locale = toSwissLocale(i18n.language as SupportedLanguage)
  useSetBreadcrumb([t('shell.nav.administration'), t('numberRanges.title')])
  const { activeDealership, isActiveDealershipManager } = useAuth()
  const dealershipId = activeDealership?.id ?? ''
  const canWrite = useCanWrite('number_ranges')
  const queryClient = useQueryClient()
  const [editing, setEditing] = useState<NumberRangeRead | null>(null)
  const [confirming, setConfirming] = useState(false)
  const [confirmError, setConfirmError] = useState<string | null>(null)

  const ranges = useQuery({
    queryKey: numberRangesKey(dealershipId),
    queryFn: () => api.get<NumberRangeList>(`/dealerships/${dealershipId}/number-ranges`),
    enabled: dealershipId !== '',
  })
  const mayChangeFrozen = ranges.data?.mayChangeFrozen ?? false

  const confirm = async () => {
    setConfirming(true)
    setConfirmError(null)
    try {
      await api.post(`/dealerships/${dealershipId}/number-range-confirmations`)
      await queryClient.invalidateQueries({ queryKey: numberRangesKey(dealershipId) })
    } catch (err) {
      setConfirmError(apiRefusalText(t, err, 'numberRanges.confirm.error'))
    } finally {
      setConfirming(false)
    }
  }

  const save = async (range: NumberRangeRead, change: NumberRangeChange) => {
    const updated = await api.patch<NumberRangeRead>(
      `/dealerships/${dealershipId}/number-ranges/${range.documentType}`,
      change,
      { 'If-Match': String(range.version) },
    )
    queryClient.setQueryData<NumberRangeList>(numberRangesKey(dealershipId), (list) =>
      list ? { ...list, items: list.items.map((item) => (item.documentType === updated.documentType ? updated : item)) } : list,
    )
  }

  return (
    <Stack gap="md">
      <div>
        <Title order={2}>{t('numberRanges.title')}</Title>
        <Text c="dimmed" fz="sm">
          {t('numberRanges.subtitle')}
        </Text>
      </div>
      <Alert color="yellow" variant="light">
        {t('numberRanges.freezeNotice')}
      </Alert>
      {ranges.isError && (
        <Alert color="red" role="alert">
          {t('numberRanges.loadError')}
        </Alert>
      )}
      {ranges.isLoading && <Skeleton height={240} radius="md" />}
      {ranges.data && isActiveDealershipManager && (
        <Paper withBorder radius="md" p="sm">
          <Group justify="space-between">
            <Text fz="sm">
              {ranges.data.confirmedAt
                ? t('numberRanges.confirm.confirmedAt', { date: formatDateTime(ranges.data.confirmedAt, locale) })
                : t('numberRanges.confirm.notConfirmed')}
            </Text>
            <Button size="xs" variant={ranges.data.confirmedAt ? 'default' : 'filled'} loading={confirming} onClick={() => void confirm()}>
              {t('numberRanges.confirm.button')}
            </Button>
          </Group>
          {confirmError && (
            <Alert color="red" role="alert" mt="xs">
              {confirmError}
            </Alert>
          )}
        </Paper>
      )}
      {ranges.data && !canWrite && (
        <Text c="dimmed" fz="sm">
          {t('numberRanges.readOnly')}
        </Text>
      )}
      {ranges.data && (
        <Paper withBorder radius="md">
          <Table striped highlightOnHover aria-label={t('numberRanges.title')}>
            <Table.Thead>
              <Table.Tr>
                <Table.Th>{t('numberRanges.columns.documentType')}</Table.Th>
                <Table.Th>{t('numberRanges.columns.pattern')}</Table.Th>
                <Table.Th>{t('numberRanges.columns.nextNumber')}</Table.Th>
                <Table.Th>{t('numberRanges.columns.currentValue')}</Table.Th>
                <Table.Th>{t('numberRanges.columns.status')}</Table.Th>
                <Table.Th aria-label={t('numberRanges.columns.actions')} />
              </Table.Tr>
            </Table.Thead>
            <Table.Tbody>
              {ranges.data.items.map((range) => {
                const frozen = range.frozenAt != null
                const editable = canWrite && (!frozen || mayChangeFrozen)
                return (
                  <Table.Tr key={range.documentType}>
                    <Table.Td>{t(`numberRanges.documentType.${range.documentType}`)}</Table.Td>
                    <Table.Td>
                      <code>{range.pattern}</code>
                      {range.yearlyReset && (
                        <Text size="xs" c="dimmed">
                          {t('numberRanges.yearlyResetShort')}
                        </Text>
                      )}
                    </Table.Td>
                    <Table.Td>{range.nextNumber}</Table.Td>
                    <Table.Td>{range.lastValue ?? '—'}</Table.Td>
                    <Table.Td>
                      {frozen ? (
                        <span title={t('numberRanges.frozenSince', { date: formatDateTime(range.frozenAt!, locale) })}>
                          <Badge tone="slate" dot>
                            {t('numberRanges.status.frozen')}
                          </Badge>
                        </span>
                      ) : (
                        <Badge tone="success" dot>
                          {t('numberRanges.status.open')}
                        </Badge>
                      )}
                    </Table.Td>
                    <Table.Td>
                      {editable && (
                        <Button size="xs" variant="subtle" onClick={() => setEditing(range)}>
                          {t('numberRanges.edit')}
                        </Button>
                      )}
                    </Table.Td>
                  </Table.Tr>
                )
              })}
            </Table.Tbody>
          </Table>
        </Paper>
      )}
      <NumberRangeDialog dealershipId={dealershipId} range={editing} onClose={() => setEditing(null)} onSave={save} />
    </Stack>
  )
}
