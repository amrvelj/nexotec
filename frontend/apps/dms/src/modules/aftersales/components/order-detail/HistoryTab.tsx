import { useMemo } from 'react'
import { Loader, Paper, Stack, Text } from '@mantine/core'
import { useQuery } from '@tanstack/react-query'
import { History } from 'lucide-react'
import { useTranslation } from 'react-i18next'
import { slate } from '@nexotec/ui-kit'
import { api } from '../../../../api/client'
import type { LabourTimeUnit, ServiceOrderHistoryEntryRead, ServiceOrderHistoryRead } from '../../../../api/types'
import { toSwissLocale, type SupportedLanguage } from '../../../../i18n'
import { formatCurrencyChf, formatDateTime } from '../../../../utils/format'
import { apiRefusalText } from '../../../../utils/apiRefusal'
import { formatLabourTime } from '../../serviceOrderOptions'

type Translate = (key: string, options?: Record<string, unknown>) => string
type Snapshot = Record<string, unknown> | null

// Every action the order's service writes to its audit trail
// (app/aftersales/services/service_order.py, positions.py, answers.py, stays.py); another reads as `otherAction`.
const KNOWN_ACTIONS = new Set([
  'service_order_created',
  'service_order_cancelled',
  'service_order_parties_changed',
  'service_order_job_added',
  'service_order_job_changed',
  'service_order_job_deleted',
  'service_order_job_accepted',
  // KAN-811 (answers.py): an answer recorded; an accepted job waiting again after its amount moved.
  'service_order_job_answered',
  'service_order_job_waits_again',
  // KAN-813 (stays.py, service_order.py): the car arrived; an order joined the running stay.
  'service_order_car_arrived',
  'service_order_joined_stay',
  'service_order_position_added',
  'service_order_position_changed',
  'service_order_position_deleted',
])

// The fields a change shows as before → after, in this order; ids and copies of them stay out.
const SHOWN_FIELDS = ['contactLabel', 'invoiceRecipientLabel', 'driverLabel', 'state', 'name', 'answerState', 'text', 'time', 'unitPrice', 'rateSource'] as const

function value(t: Translate, field: (typeof SHOWN_FIELDS)[number], snapshot: Snapshot): string {
  const raw = snapshot?.[field]
  if (raw === null || raw === undefined || raw === '') return '—'
  const text = String(raw)
  switch (field) {
    case 'state':
      return t(`serviceOrder.state.${text}`)
    case 'answerState':
      return t(`serviceOrder.answerState.${text}`)
    case 'rateSource':
      return t(`serviceOrder.rateSource.${text}`)
    case 'time': {
      const unit = snapshot?.timeUnit as LabourTimeUnit | undefined
      return unit ? formatLabourTime(t, text, unit) : text
    }
    case 'unitPrice': {
      const entered = snapshot?.priceEntered as string | undefined
      const amount = Number.isFinite(Number(text)) ? formatCurrencyChf(Number(text)) : text
      return entered ? `${amount} ${t(`serviceOrder.history.priceEntered.${entered}`)}` : amount
    }
    default:
      return text
  }
}

function subject(entry: ServiceOrderHistoryEntryRead): string | null {
  const side = entry.after ?? entry.before
  const named = side?.name ?? side?.text
  return typeof named === 'string' && named !== '' ? named : null
}

function Entry({ entry, locale }: { entry: ServiceOrderHistoryEntryRead; locale: string }) {
  const { t } = useTranslation()
  const action = KNOWN_ACTIONS.has(entry.action) ? t(`serviceOrder.history.actions.${entry.action}`) : t('serviceOrder.history.otherAction')
  const named = subject(entry)
  const headline = named ? t('serviceOrder.history.withSubject', { action, subject: named }) : action
  const actor = entry.actorId ? (entry.actorLabel ?? t('serviceOrder.history.unknownUser')) : t('serviceOrder.history.system')
  const changes =
    entry.before && entry.after
      ? SHOWN_FIELDS.filter((field) => JSON.stringify(entry.before?.[field] ?? null) !== JSON.stringify(entry.after?.[field] ?? null) ||
          (field === 'time' && entry.before?.timeUnit !== entry.after?.timeUnit) ||
          (field === 'unitPrice' && entry.before?.priceEntered !== entry.after?.priceEntered))
      : []
  return (
    <div
      data-testid="history-entry"
      style={{ display: 'grid', gridTemplateColumns: '150px 1fr auto', gap: 16, padding: '8px 0', borderBottom: `1px solid ${slate[1]}` }}
    >
      <Text size="sm" ff="monospace" c="dimmed">
        {formatDateTime(entry.createdAt, locale)}
      </Text>
      <Stack gap={2}>
        <Text size="sm">{headline}</Text>
        {entry.reason && (
          <Text size="xs" c="dimmed">
            {t('serviceOrder.history.reason', { reason: entry.reason })}
          </Text>
        )}
        {changes.map((field) => (
          <Text key={field} size="xs" c="dimmed">
            <Text component="span" fw={500} c={slate[7]}>
              {t(`serviceOrder.history.fields.${field}`)}
            </Text>
            : {value(t, field, entry.before)} → {value(t, field, entry.after)}
          </Text>
        ))}
      </Stack>
      <Text size="sm" c="dimmed">
        {actor}
      </Text>
    </div>
  )
}

/**
 * D3 «Verlauf» (KAN-810 criterion 6): the order's history is the platform's
 * audit trail (`GET …/history`), newest first — what happened, to which job
 * or position, who and when, and what changed (before → after) in the user's
 * language, as the contact's history tab renders its audit events.
 */
export function HistoryTab({ orderId, version }: { orderId: string; version: number }) {
  const { t, i18n } = useTranslation()
  const locale = toSwissLocale(i18n.language as SupportedLanguage)
  const history = useQuery({
    // The version is in the key: every change of the order writes an entry.
    queryKey: ['service-order', orderId, 'history', version],
    queryFn: () => api.get<ServiceOrderHistoryRead>(`/service-orders/${orderId}/history`),
  })
  const entries = useMemo(
    () => [...(history.data?.items ?? [])].sort((a, b) => b.createdAt.localeCompare(a.createdAt)),
    [history.data],
  )

  return (
    <Paper withBorder radius="md" p="lg">
      <Stack gap="xs">
        <Text fw={600} style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
          <History size={18} />
          {t('serviceOrder.history.title')}
        </Text>
        {history.isLoading && <Loader size="sm" />}
        {history.isError && (
          <Text size="sm" c="red">
            {apiRefusalText(t, history.error, 'serviceOrder.history.loadError')}
          </Text>
        )}
        {history.isSuccess && entries.length === 0 && (
          <Text size="sm" c="dimmed">
            {t('serviceOrder.history.empty')}
          </Text>
        )}
        {entries.map((entry) => (
          <Entry key={entry.id} entry={entry} locale={locale} />
        ))}
      </Stack>
    </Paper>
  )
}
