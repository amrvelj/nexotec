import { useQuery } from '@tanstack/react-query'
import { Loader } from '@mantine/core'
import { useTranslation } from 'react-i18next'
import { KeyValueRow, OverviewCard, slate } from '@nexotec/ui-kit'
import { api } from '../../api/client'
import type { StockValuationRefRead } from '../../api/types'
import { formatCurrencyChf, formatDateTime } from '../../utils/format'
import { ValuationSourceMarker } from '../ValuationSourceMarker'

interface EvaluationTabProps {
  stockItemId: string
  locale: string
}

/**
 * § ADR-066/ADR-048 — Stock is a READER only. No form, no mutation: the
 * valuation module owns creation, the list and the status. This tab
 * renders the denormalized pointer Stock holds — set on a trade-in's
 * pipeline item when its contract is confirmed (KAN-101) — and marks a
 * manual figure as manual.
 */
export function EvaluationTab({ stockItemId, locale }: EvaluationTabProps) {
  const { t } = useTranslation()
  const query = useQuery({
    queryKey: ['stock-item', stockItemId, 'valuation'],
    queryFn: () => api.get<StockValuationRefRead>(`/inventory/stock-items/${stockItemId}/valuation`),
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
          <KeyValueRow label={t('stockDetail.evaluation.source')}>
            <ValuationSourceMarker source={ref.source} />
          </KeyValueRow>
        </>
      )}
      <p style={{ fontSize: 12, color: slate[5], marginTop: 12 }}>{t('stockDetail.evaluation.hint')}</p>
    </OverviewCard>
  )
}
