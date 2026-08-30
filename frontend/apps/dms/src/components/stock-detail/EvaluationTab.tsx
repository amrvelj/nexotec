import { useQuery } from '@tanstack/react-query'
import { Loader } from '@mantine/core'
import { useTranslation } from 'react-i18next'
import { KeyValueRow, OverviewCard, slate } from '@nexotec/ui-kit'
import { api } from '../../api/client'
import { formatCurrencyChf, formatDateTime } from '../../utils/format'

interface ValuationRefRead {
  valuationId: string | null
  amount: string | null
  valuedAt: string | null
  source: string | null
}

interface EvaluationTabProps {
  stockItemId: string
  locale: string
}

/**
 * § ADR-066/ADR-048 — Stock is a READER only. No form, no mutation: the
 * real valuation module (creation, list, draft/valid/expired/used status
 * derivation) is WP-8 scope. This tab renders whatever denormalized
 * pointer Stock currently holds and nothing else — building a local
 * create form here would be exactly the "wrong package" mistake the
 * brief warns against.
 */
export function EvaluationTab({ stockItemId, locale }: EvaluationTabProps) {
  const { t } = useTranslation()
  const query = useQuery({
    queryKey: ['stock-item', stockItemId, 'valuation'],
    queryFn: () => api.get<ValuationRefRead>(`/inventory/stock-items/${stockItemId}/valuation`),
  })

  if (query.isLoading) return <Loader />
  const ref = query.data

  return (
    <OverviewCard title={t('stockDetail.evaluation.title')}>
      {!ref?.valuationId ? (
        <span style={{ fontSize: 13, color: slate[5] }}>{t('stockDetail.evaluation.empty')}</span>
      ) : (
        <>
          <KeyValueRow label={t('stockDetail.evaluation.amount')}>
            {ref.amount != null ? formatCurrencyChf(Number(ref.amount)) : '—'}
          </KeyValueRow>
          <KeyValueRow label={t('stockDetail.evaluation.valuedAt')}>
            {ref.valuedAt ? formatDateTime(ref.valuedAt, locale) : '—'}
          </KeyValueRow>
          <KeyValueRow label={t('stockDetail.evaluation.source')}>{ref.source ?? '—'}</KeyValueRow>
        </>
      )}
      <p style={{ fontSize: 12, color: slate[5], marginTop: 12 }}>{t('stockDetail.evaluation.hint')}</p>
    </OverviewCard>
  )
}
