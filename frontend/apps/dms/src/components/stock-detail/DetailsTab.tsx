import { useEffect, useId, useState } from 'react'
import { Button, Group, Text } from '@mantine/core'
import { FieldLabel, InlineEditField, KeyValueRow, OverviewCard, ProgressiveDisclosure, SpecGrid, chfInlineEditor, slate } from '@nexotec/ui-kit'
import { HostConfigurationCard } from '../configurator/HostConfigurationCard'
import { useTranslation } from 'react-i18next'
import { useQueryClient } from '@tanstack/react-query'
import { api } from '../../api/client'
import { useInlineEditRefusal } from '../../hooks/useInlineEditRefusal'
import { isStaleVersion } from '../../utils/apiRefusal'
import { formatCurrencyChf, formatDateOnly, formatNumber } from '../../utils/format'
import { AttachConfigurationButton } from './AttachConfigurationButton'
import { useAttachConfiguration } from './useAttachConfiguration'
import { PurchaseStornoDialog } from './PurchaseStornoDialog'
import { RecordPurchaseDialog } from './RecordPurchaseDialog'
import { useStockConfigurationEditor } from './useStockConfigurationEditor'
import type { ConfigurationRead, StockItemRead } from '../../api/types'

export type DetailsTabAction = 'recordPurchase' | 'stornoPurchase' | 'attachConfiguration' | 'editConfiguration'

interface DetailsTabProps {
  item: StockItemRead
  locale: string
  onSaveField: (patch: Record<string, unknown>) => Promise<void>
  onReload: () => void
  /** KAN-430: whether the user's role may book the purchase
   * (`stock_acquisition`). Hidden when not; the stock detail passes the
   * session's answer. */
  canRecordPurchase?: boolean
  /** KAN-430: whether the user's role may link a configuration
   * (`stock_items` and `configurations`); without, the card only says one is
   * missing. */
  canAttachConfiguration?: boolean
  /** KAN-510: whether the user's role may write configurations
   * (`configurations`) — «Konfiguration bearbeiten» on the card and beside
   * the purchase dialog's VIN refusal. Hidden when not. */
  canEditConfiguration?: boolean
  /** KAN-146: the stock item after «Einkauf stornieren» reversed its
   * purchase. Offered beside a booked purchase to the same role that books it
   * (`canRecordPurchase`). */
  onPurchaseStornoed?: (updated: StockItemRead) => void
  /** KAN-571: an action the stock row menu asked for (the header's overflow,
   * or the Stock list through the router state) whose dialog or configurator
   * lives on this tab. It runs once; `onRequestedActionHandled` clears it. An
   * action this car or role does not allow (a hand-made router state) is
   * dropped, as the menu would never offer it. */
  requestedAction?: DetailsTabAction | null
  onRequestedActionHandled?: () => void
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

export function DetailsTab({
  item,
  locale,
  onSaveField,
  onReload,
  canRecordPurchase = true,
  canAttachConfiguration = true,
  canEditConfiguration = true,
  onPurchaseStornoed,
  requestedAction = null,
  onRequestedActionHandled,
  onRecordPurchase,
}: DetailsTabProps) {
  const { t } = useTranslation()
  const { describeError, reloadLabel } = useInlineEditRefusal()
  const [purchaseDialogOpen, setPurchaseDialogOpen] = useState(false)
  const [stornoDialogOpen, setStornoDialogOpen] = useState(false)
  const stornoReasonId = useId()
  const editConfiguration = useStockConfigurationEditor()
  // KAN-510 (Anto, 2026-11-02, option A): «Konfiguration bearbeiten» on
  // every stock car, in the pipeline or in stock (not one that has left
  // stock), unless a contract reserves it. Then the change belongs to that
  // contract: the card offers «Vertrag bearbeiten» instead, disabled with the
  // reason. A contract reserves a car as it is signed (`reserve_for_contract`
  // in the confirmation, and the item a signed manual configuration creates),
  // and a signed contract is frozen (ADR-083), so it is cancelled first. A
  // pending contract holds the reservation only while its confirmation runs
  // (or, after a failed one, until the nightly sweep frees it), so the reason
  // names no status.
  const reservedByContract = item.reservationState === 'reserved'
  const onStock = (item.lifecycleStatus === 'pipeline' || item.lifecycleStatus === 'in_stock') && item.leftStockAt == null
  const mayEditConfiguration = canEditConfiguration && onStock && !reservedByContract
  const reservedReasonId = useId()
  const queryClient = useQueryClient()
  const attachConfiguration = useAttachConfiguration(item)

  // KAN-571 — the row menu's actions that open this tab's own dialogs.
  useEffect(() => {
    if (!requestedAction) return
    onRequestedActionHandled?.()
    const booked = item.purchasePrice != null
    const onStockNow = item.leftStockAt == null
    if (requestedAction === 'recordPurchase' && canRecordPurchase && !booked && onStockNow) {
      setPurchaseDialogOpen(true)
    } else if (requestedAction === 'stornoPurchase' && canRecordPurchase && onPurchaseStornoed && booked && onStockNow) {
      setStornoDialogOpen(true)
    } else if (requestedAction === 'attachConfiguration' && canAttachConfiguration && !item.configurationId) {
      attachConfiguration()
    } else if (requestedAction === 'editConfiguration' && mayEditConfiguration && item.configurationId) {
      const configurationId = item.configurationId
      void queryClient
        .fetchQuery({
          queryKey: ['configuration', configurationId],
          queryFn: () => api.get<ConfigurationRead>(`/configurations/${configurationId}`),
        })
        .then((configuration) => editConfiguration(configuration))
        .catch(() => undefined) // the card says the configuration could not be read
    }
    // Runs once per request; the handlers are recreated on every render.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [requestedAction])

  // KAN-510 — from the purchase dialog's VIN refusal: the dialog steps aside
  // (a modal would hold the focus the configurator needs) and comes back,
  // with what was entered and the corrected VIN, when the configurator closes.
  const editConfigurationFromPurchase = (configuration: ConfigurationRead) => {
    setPurchaseDialogOpen(false)
    editConfiguration(configuration, () => setPurchaseDialogOpen(true))
  }

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
          {/* KAN-510: «Konfiguration bearbeiten» — the configurator on this
              configuration, in its own mode; the only write is its PATCH.
              Only where `mayEditConfiguration` allows it (above). */}
          <HostConfigurationCard
            configurationId={item.configurationId}
            technicalDataColumns={4}
            onOpenConfigurator={mayEditConfiguration ? (configuration) => editConfiguration(configuration) : undefined}
            openConfiguratorLabel={t('stockDetail.configuration.edit')}
          />
          {/* An action, not a field: its reason is said (ADR-061). */}
          {canEditConfiguration && onStock && reservedByContract && (
            <Group gap="sm" mt="sm" wrap="nowrap" align="flex-start">
              <Button variant="default" size="xs" disabled aria-describedby={reservedReasonId} style={{ flexShrink: 0 }}>
                {t('stockDetail.configuration.editContract')}
              </Button>
              <Text id={reservedReasonId} size="sm" c="dimmed">
                {t('stockDetail.configuration.reservedByContract')}
              </Text>
            </Group>
          )}
        </OverviewCard>
      ) : (
        // KAN-463 — an item the retired hand-typed intake created has no
        // configuration, so no technical data; it is repaired here.
        <OverviewCard title={t('stockDetail.configuration.title')}>
          <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', gap: 16, flexWrap: 'wrap' }}>
            <span style={{ color: slate[5] }}>{t('stockDetail.configuration.missing')}</span>
            {canAttachConfiguration && <AttachConfigurationButton item={item} />}
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
            {item.configurationId ? (
              // ADR-117 (2), KAN-556: a configured car is named by its
              // configuration and follows it — changed there, never here.
              // The hint is the kit's info icon (KAN-443), reachable by keyboard and touch.
              <FieldLabel
                label={<span data-testid="vehicle-label-read-only">{item.vehicleLabel}</span>}
                hint={t('stockDetail.identity.vehicleLabelFollowsConfiguration')}
                labels={{ icon: t('common.fieldHintIcon') }}
              />
            ) : (
              <InlineEditField
                value={item.vehicleLabel}
                emptyLabel={t('common.notSet')}
                onSave={(v) => onSaveField({ vehicleLabel: v })}
                describeError={describeError}
                isConflict={isStaleVersion}
                onReload={onReload}
                reloadLabel={reloadLabel}
              />
            )}
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
              {/* KAN-146 (FR-I-05): an action, not a field — on a car that has
                  left stock it stays visible, disabled, and says why (ADR-061). */}
              {canRecordPurchase && onPurchaseStornoed && (
                <Group gap="sm" mt="sm" wrap="nowrap" align="flex-start">
                  <Button
                    variant="default"
                    size="xs"
                    color="red"
                    disabled={item.leftStockAt != null}
                    aria-describedby={item.leftStockAt != null ? stornoReasonId : undefined}
                    onClick={() => setStornoDialogOpen(true)}
                    style={{ flexShrink: 0 }}
                  >
                    {t('stockDetail.purchase.storno.button')}
                  </Button>
                  {item.leftStockAt != null && (
                    <Text id={stornoReasonId} size="sm" c="dimmed">
                      {t('stockDetail.purchase.storno.leftStock')}
                    </Text>
                  )}
                </Group>
              )}
            </OverviewCard>
          )}
        </div>
      </ProgressiveDisclosure>

      {onPurchaseStornoed && (
        <PurchaseStornoDialog
          opened={stornoDialogOpen}
          onClose={() => setStornoDialogOpen(false)}
          item={item}
          onStornoed={onPurchaseStornoed}
        />
      )}
      <RecordPurchaseDialog
        opened={purchaseDialogOpen}
        onClose={() => setPurchaseDialogOpen(false)}
        // KAN-146: a stornoed car is back in pipeline with its VIN and is
        // bought again under it — the dialog asks for none.
        requiresVin={item.lifecycleStatus === 'pipeline' && item.vin == null}
        configurationId={item.configurationId ?? null}
        onEditConfiguration={mayEditConfiguration ? editConfigurationFromPurchase : undefined}
        onSubmit={onRecordPurchase}
      />
    </div>
  )
}
