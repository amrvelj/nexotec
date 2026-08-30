import { useState } from 'react'
import { Button } from '@mantine/core'
import { useTranslation } from 'react-i18next'
import { radius, semantic, slate, spacing } from '@nexotec/ui-kit'
import { formatCurrencyChf, formatDate } from '../../utils/format'
import { translatedLedgerCategoryLabel } from '../../stockOptions'
import { RecordCostDialog } from './RecordCostDialog'
import type { LedgerCategory, LedgerEntryRead } from '../../api/types'

interface WagenbuchTabProps {
  entries: LedgerEntryRead[]
  locale: string
  onRecordCost: (data: { category: LedgerCategory; amount: number; occurredAt: string; sourceRef: string }) => Promise<void>
}

/**
 * § FR-I-15a — the Wagenbuch (ADR-029, entity-private, never group-
 * readable). A plain hand-built list, not the full DataGrid: an embedded,
 * entity-scoped, unpaginated few-row history is the same shape as
 * CustomerDetail's own HistoryTab, which uses the identical pattern
 * rather than the top-level overview grid machinery.
 */
export function WagenbuchTab({ entries, locale, onRecordCost }: WagenbuchTabProps) {
  const { t } = useTranslation()
  const [dialogOpen, setDialogOpen] = useState(false)

  const total = entries.reduce(
    (sum, e) => sum + (e.direction === 'revenue' ? Number(e.amount) : -Number(e.amount)),
    0
  )

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: spacing.md }}>
      <div
        style={{
          display: 'flex',
          justifyContent: 'space-between',
          alignItems: 'center',
          padding: spacing.md,
          borderRadius: radius.md,
          backgroundColor: total >= 0 ? semantic.success.surface : semantic.destructive.surface,
          color: total >= 0 ? semantic.success.text : semantic.destructive.text,
        }}
      >
        <span style={{ fontWeight: 600 }}>{t('stockDetail.wagenbuch.result')}</span>
        <span style={{ fontWeight: 700, fontSize: 18 }}>{formatCurrencyChf(total)}</span>
      </div>

      <Button variant="default" onClick={() => setDialogOpen(true)} style={{ alignSelf: 'flex-start' }}>
        {t('stockDetail.wagenbuch.recordButton')}
      </Button>

      {entries.length === 0 ? (
        <span style={{ color: slate[5], fontSize: 14 }}>{t('stockDetail.wagenbuch.empty')}</span>
      ) : (
        <div style={{ overflowX: 'auto' }}>
          <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: 14 }}>
            <thead>
              <tr style={{ borderBottom: `1px solid ${slate[2]}`, textAlign: 'left', color: slate[5] }}>
                <th style={{ padding: spacing.sm }}>{t('stockDetail.wagenbuch.columns.occurredAt')}</th>
                <th style={{ padding: spacing.sm }}>{t('stockDetail.wagenbuch.columns.category')}</th>
                <th style={{ padding: spacing.sm, textAlign: 'right' }}>{t('stockDetail.wagenbuch.columns.amount')}</th>
                <th style={{ padding: spacing.sm }}>{t('stockDetail.wagenbuch.columns.source')}</th>
              </tr>
            </thead>
            <tbody>
              {entries.map((entry) => (
                <tr key={entry.id} style={{ borderBottom: `1px solid ${slate[1]}` }}>
                  <td style={{ padding: spacing.sm }}>{formatDate(entry.occurredAt, locale)}</td>
                  <td style={{ padding: spacing.sm }}>{translatedLedgerCategoryLabel(t, entry.category)}</td>
                  <td
                    style={{
                      padding: spacing.sm,
                      textAlign: 'right',
                      color: entry.direction === 'revenue' ? semantic.success.text : slate[9],
                    }}
                  >
                    {entry.direction === 'revenue' ? '+' : '−'}
                    {formatCurrencyChf(Number(entry.amount))}
                  </td>
                  <td style={{ padding: spacing.sm, color: slate[5] }}>
                    {entry.isAuto ? t('stockDetail.wagenbuch.auto') : t('stockDetail.wagenbuch.manual')}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      <RecordCostDialog opened={dialogOpen} onClose={() => setDialogOpen(false)} onSubmit={onRecordCost} />
    </div>
  )
}
