import { useState } from 'react'
import { Button } from '@mantine/core'
import { InlineEditField, KeyValueRow, OverviewCard, ProgressiveDisclosure, SpecGrid, chfInlineEditor, slate } from '@nexotec/ui-kit'
import { HostConfigurationCard } from '../configurator/HostConfigurationCard'
import { useTranslation } from 'react-i18next'
import { useInlineEditRefusal } from '../../hooks/useInlineEditRefusal'
import { isStaleVersion } from '../../utils/apiRefusal'
import { formatCurrencyChf, formatDateOnly, formatNumber } from '../../utils/format'
import { AttachConfigurationButton } from './AttachConfigurationButton'
import { RecordPurchaseDialog } from './RecordPurchaseDialog'
import type { StockItemRead } from '../../api/types'

interface DetailsTabProps {
  item: StockItemRead
  locale: string
  onSaveField: (patch: Record<string, unknown>) => Promise<void>
  onReload: () => void
  /** KAN-430: whether the user's role may book the purchase
   * (`stock_acquisition`). Hidden when not; the stock detail passes the
   * session's answer. */
  canRecordPurchase?: boolean
  onRecordPurchase: (data: {
    supplierName: string
    supplierIsVatRegistered: boolean
    purchasePrice: number
    purchaseDate: string
    purchaseInvoiceRef?: string
    vin?: string
  }) => Promise<void>
}

/**
 * § UI/UX Core Principles — Details tab, FR-I-07's fixed block order.
 * PR-1 scope only: hero + spec + commercial. Pictures (read-only here,
 * managed only in the Publikation tab per the live prototype's own
 * caption), equipment summary and marketplace summary all arrive with
 * PR-8; the evaluation/valuation reader arrives with PR-9.
 */
// KAN-168: the price editors open grouped, with CHF (CHF 42'500), never on the raw 42500.00.
const chfEditor = chfInlineEditor({ min: 0 })

export function DetailsTab({ item, locale, onSaveField, onReload, canRecordPurchase = true, onRecordPurchase }: DetailsTabProps) {
  const { t } = useTranslation()
  const { describeError, reloadLabel } = useInlineEditRefusal()
  const [purchaseDialogOpen, setPurchaseDialogOpen] = useState(false)

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 16 }}>
      <div style={{ fontSize: 28, fontWeight: 700 }}>
        {/* KAN-359 (Anto, option A): until a selling price is built, the
            headline is the list price, captioned as such, never "not set". */}
        {item.effectivePrice != null
          ? formatCurrencyChf(Number(item.effectivePrice))
          : item.listPrice != null
            ? formatCurrencyChf(Number(item.listPrice))
            : t('common.notSet')}
        {item.listPrice != null && item.effectivePrice != null && item.listPrice !== item.effectivePrice && (
          <span style={{ marginLeft: 10, fontSize: 16, color: slate[4], textDecoration: 'line-through' }}>
            {formatCurrencyChf(Number(item.listPrice))}
          </span>
        )}
        {item.effectivePrice == null && item.listPrice != null && (
          <div style={{ fontSize: 13, fontWeight: 400, color: slate[5] }}>{t('stockDetail.headline.listPriceCaption')}</div>
        )}
      </div>

      <SpecGrid
        columns={4}
        items={[
          { label: t('stockDetail.spec.firstRegistrationDate'), value: item.firstRegistrationDate ? formatDateOnly(item.firstRegistrationDate, locale) : '—' },
          { label: t('stockDetail.spec.odometerKm'), value: item.odometerKm != null ? `${formatNumber(item.odometerKm)} km` : '—' },
          { label: t('stockDetail.spec.stockNumber'), value: item.stockNumber },
          { label: t('stockDetail.spec.vin'), value: item.vin ?? t('common.notSet') },
        ]}
      />

      {item.configurationId ? (
        // C-F (KAN-10, FR-C-13) — the configuration this pipeline item points at.
        // KAN-459: it carries block 2 of FR-I-07's order, the technical data,
        // straight after the hero band, in a spec grid of at most four columns.
        <OverviewCard title={t('stockDetail.configuration.title')}>
          <HostConfigurationCard configurationId={item.configurationId} technicalDataColumns={4} />
        </OverviewCard>
      ) : (
        // KAN-463 — an item the retired hand-typed intake created has no
        // configuration, so no technical data; it is repaired here.
        <OverviewCard title={t('stockDetail.configuration.title')}>
          <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', gap: 16, flexWrap: 'wrap' }}>
            <span style={{ color: slate[5] }}>{t('stockDetail.configuration.missing')}</span>
            <AttachConfigurationButton item={item} />
          </div>
        </OverviewCard>
      )}

      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(320px, 1fr))', gap: 16 }}>
        <OverviewCard title={t('stockDetail.commercial.title')}>
          <KeyValueRow label={t('stockDetail.commercial.odometerKm')}>
            <InlineEditField
              value={item.odometerKm != null ? formatNumber(item.odometerKm) : ''}
              editValue={item.odometerKm != null ? String(item.odometerKm) : ''}
              isEmpty={item.odometerKm == null}
              emptyLabel={t('common.notSet')}
              onSave={(v) => onSaveField({ odometerKm: v ? Number(v) : null })}
              describeError={describeError}
              isConflict={isStaleVersion}
              onReload={onReload}
              reloadLabel={reloadLabel}
            />
          </KeyValueRow>
          <KeyValueRow label={t('stockDetail.commercial.listPrice')}>
            <InlineEditField
              value={item.listPrice != null ? formatCurrencyChf(Number(item.listPrice)) : ''}
              editValue={item.listPrice ?? ''}
              isEmpty={item.listPrice == null}
              emptyLabel={t('common.notSet')}
              onSave={(v) => onSaveField({ listPrice: v || null })}
              renderEditor={chfEditor}
              describeError={describeError}
              isConflict={isStaleVersion}
              onReload={onReload}
              reloadLabel={reloadLabel}
            />
          </KeyValueRow>
          <KeyValueRow label={t('stockDetail.commercial.effectivePrice')}>
            <InlineEditField
              value={item.effectivePrice != null ? formatCurrencyChf(Number(item.effectivePrice)) : ''}
              editValue={item.effectivePrice ?? ''}
              isEmpty={item.effectivePrice == null}
              emptyLabel={t('common.notSet')}
              onSave={(v) => onSaveField({ effectivePrice: v || null })}
              renderEditor={chfEditor}
              describeError={describeError}
              isConflict={isStaleVersion}
              onReload={onReload}
              reloadLabel={reloadLabel}
            />
          </KeyValueRow>
        </OverviewCard>

        <OverviewCard title={t('stockDetail.identity.title')}>
          <KeyValueRow label={t('stockDetail.identity.vehicleLabel')}>
            <InlineEditField
              value={item.vehicleLabel}
              emptyLabel={t('common.notSet')}
              onSave={(v) => onSaveField({ vehicleLabel: v })}
              describeError={describeError}
              isConflict={isStaleVersion}
              onReload={onReload}
              reloadLabel={reloadLabel}
            />
          </KeyValueRow>
          <KeyValueRow label={t('stockDetail.identity.vehicleId')}>
            <span style={{ fontFamily: 'monospace', fontSize: 12 }}>{item.vehicleId ?? t('common.notSet')}</span>
          </KeyValueRow>
        </OverviewCard>
      </div>

      <ProgressiveDisclosure label={t('stockDetail.purchase.title')}>
        <div style={{ display: 'flex', flexDirection: 'column', gap: 12 }}>
          {item.purchasePrice == null ? (
            canRecordPurchase ? (
              <Button variant="default" onClick={() => setPurchaseDialogOpen(true)} style={{ alignSelf: 'flex-start' }}>
                {t('stockDetail.purchase.recordButton')}
              </Button>
            ) : (
              <span style={{ color: slate[5] }}>{t('stockDetail.purchase.notRecorded')}</span>
            )
          ) : (
            <OverviewCard title={t('stockDetail.purchase.title')}>
              <KeyValueRow label={t('stockDetail.purchase.fields.supplierName')}>{item.supplierName}</KeyValueRow>
              <KeyValueRow label={t('stockDetail.purchase.fields.purchasePrice')}>
                {formatCurrencyChf(Number(item.purchasePrice))}
              </KeyValueRow>
              <KeyValueRow label={t('stockDetail.purchase.fields.purchaseDate')}>
                {item.purchaseDate ? formatDateOnly(item.purchaseDate, locale) : '—'}
              </KeyValueRow>
              <KeyValueRow label={t('stockDetail.purchase.notionalInputTax')}>
                {item.notionalInputTaxApplicable
                  ? item.notionalInputTaxAmount != null
                    ? formatCurrencyChf(Number(item.notionalInputTaxAmount))
                    : t('stockDetail.purchase.notionalInputTaxRateNotConfigured')
                  : t('stockDetail.purchase.notionalInputTaxNotApplicable')}
              </KeyValueRow>
              <KeyValueRow label={t('stockDetail.purchase.isInvoiceable')}>
                {item.isInvoiceable ? t('common.yes') : t('common.no')}
              </KeyValueRow>
            </OverviewCard>
          )}
        </div>
      </ProgressiveDisclosure>

      <RecordPurchaseDialog
        opened={purchaseDialogOpen}
        onClose={() => setPurchaseDialogOpen(false)}
        requiresVin={item.lifecycleStatus === 'pipeline'}
        onSubmit={onRecordPurchase}
      />
    </div>
  )
}
