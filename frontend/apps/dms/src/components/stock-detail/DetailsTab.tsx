import { InlineEditField, KeyValueRow, OverviewCard, SpecGrid, slate } from '@nexotec/ui-kit'
import { useTranslation } from 'react-i18next'
import { ApiError } from '../../api/client'
import { formatCurrencyChf, formatDate } from '../../utils/format'
import type { StockItemRead } from '../../api/types'

interface DetailsTabProps {
  item: StockItemRead
  locale: string
  onSaveField: (patch: Record<string, unknown>) => Promise<void>
  onReload: () => void
}

/**
 * § UI/UX Core Principles — Details tab, FR-I-07's fixed block order.
 * PR-1 scope only: hero + spec + commercial. Pictures (read-only here,
 * managed only in the Publikation tab per the live prototype's own
 * caption), equipment summary and marketplace summary all arrive with
 * PR-8; the evaluation/valuation reader arrives with PR-9.
 */
export function DetailsTab({ item, locale, onSaveField, onReload }: DetailsTabProps) {
  const { t } = useTranslation()
  const isConflict = (err: unknown) => err instanceof ApiError && err.status === 409

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 16 }}>
      <div style={{ fontSize: 28, fontWeight: 700 }}>
        {item.effectivePrice != null ? formatCurrencyChf(Number(item.effectivePrice)) : t('common.notSet')}
        {item.listPrice != null && item.effectivePrice != null && item.listPrice !== item.effectivePrice && (
          <span style={{ marginLeft: 10, fontSize: 16, color: slate[4], textDecoration: 'line-through' }}>
            {formatCurrencyChf(Number(item.listPrice))}
          </span>
        )}
      </div>

      <SpecGrid
        columns={4}
        items={[
          { label: t('stockDetail.spec.firstRegistrationDate'), value: item.firstRegistrationDate ? formatDate(item.firstRegistrationDate, locale) : '—' },
          { label: t('stockDetail.spec.odometerKm'), value: item.odometerKm != null ? `${item.odometerKm.toLocaleString(locale)} km` : '—' },
          { label: t('stockDetail.spec.stockNumber'), value: item.stockNumber },
          { label: t('stockDetail.spec.vin'), value: item.vin ?? t('common.notSet') },
        ]}
      />

      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(320px, 1fr))', gap: 16 }}>
        <OverviewCard title={t('stockDetail.commercial.title')}>
          <KeyValueRow label={t('stockDetail.commercial.odometerKm')}>
            <InlineEditField
              value={item.odometerKm != null ? String(item.odometerKm) : ''}
              isEmpty={item.odometerKm == null}
              emptyLabel={t('common.notSet')}
              onSave={(v) => onSaveField({ odometerKm: v ? Number(v) : null })}
              isConflict={isConflict}
              onReload={onReload}
            />
          </KeyValueRow>
          <KeyValueRow label={t('stockDetail.commercial.listPrice')}>
            <InlineEditField
              value={item.listPrice ?? ''}
              isEmpty={item.listPrice == null}
              emptyLabel={t('common.notSet')}
              onSave={(v) => onSaveField({ listPrice: v || null })}
              isConflict={isConflict}
              onReload={onReload}
            />
          </KeyValueRow>
          <KeyValueRow label={t('stockDetail.commercial.effectivePrice')}>
            <InlineEditField
              value={item.effectivePrice ?? ''}
              isEmpty={item.effectivePrice == null}
              emptyLabel={t('common.notSet')}
              onSave={(v) => onSaveField({ effectivePrice: v || null })}
              isConflict={isConflict}
              onReload={onReload}
            />
          </KeyValueRow>
        </OverviewCard>

        <OverviewCard title={t('stockDetail.identity.title')}>
          <KeyValueRow label={t('stockDetail.identity.vehicleLabel')}>
            <InlineEditField
              value={item.vehicleLabel}
              onSave={(v) => onSaveField({ vehicleLabel: v })}
              isConflict={isConflict}
              onReload={onReload}
            />
          </KeyValueRow>
          <KeyValueRow label={t('stockDetail.identity.vehicleId')}>
            <span style={{ fontFamily: 'monospace', fontSize: 12 }}>{item.vehicleId ?? t('common.notSet')}</span>
          </KeyValueRow>
        </OverviewCard>
      </div>
    </div>
  )
}
