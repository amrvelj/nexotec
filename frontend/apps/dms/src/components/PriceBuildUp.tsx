import { useEffect } from 'react'
import { Group, NumberInput, Select, Stack, Text } from '@mantine/core'
import { useTranslation } from 'react-i18next'
import { useDebouncedNumberField } from '../hooks/useDebouncedNumberField'
import { formatCurrencyChf } from '../utils/format'
import type { SalesPricedDealRead } from '../api/types'

export interface PriceBuildUpProps {
  /** An offer draft or a pending contract (KAN-67) — the same build-up. */
  deal: SalesPricedDealRead
  onDiscountChange: (patch: { discountType: string | null; discountValue: number | null }) => void
  onManualBasePriceChange?: (value: number | null) => void
  /** KAN-288 — told whether a typed figure is still waiting to be saved, so a
   * host that ends the edit (signing a contract) can wait for it. */
  onUnsavedChange?: (unsaved: boolean) => void
}

function row(label: string, value: string, opts?: { bold?: boolean; negative?: boolean }) {
  return (
    <Group justify="space-between" py={4}>
      <Text size="sm" c={opts?.bold ? undefined : 'dimmed'} fw={opts?.bold ? 600 : 400}>
        {label}
      </Text>
      <Text size="sm" fw={opts?.bold ? 700 : 500} c={opts?.negative ? 'red' : undefined}>
        {value}
      </Text>
    </Group>
  )
}

/**
 * WP-8 PR-3 — base -> options -> list -> accessories -> total -> discount
 * -> price (FR-S's own level order), confirmed live against the reference
 * prototype's own Preisaufbau tab. The "NUR INTERN — NICHT AUF DEM
 * DOKUMENT" block (margin/cost) is visually separated and rendered only
 * because this whole page is a seller-facing screen — the customer
 * document (PR-7) is built from a completely separate ContentDefinition
 * that never includes these fields at all (see
 * tests/architecture/test_margin_never_in_rendered_document.py).
 */
export function PriceBuildUp({ deal, onDiscountChange, onManualBasePriceChange, onUnsavedChange }: PriceBuildUpProps) {
  const { t } = useTranslation()

  // KAN-8 — local, debounced state; see useDebouncedNumberField's own
  // docstring for why a NumberInput bound straight to a per-keystroke
  // autosave silently drops digits.
  const [manualBasePrice, setManualBasePrice, manualBasePriceUnsaved] = useDebouncedNumberField(
    deal.manualBasePrice != null ? Number(deal.manualBasePrice) : null,
    deal.id,
    (value) => onManualBasePriceChange?.(value)
  )
  const [discountValue, setDiscountValue, discountValueUnsaved] = useDebouncedNumberField(
    deal.discountValue != null ? Number(deal.discountValue) : null,
    deal.id,
    (value) => onDiscountChange({ discountType: deal.discountType, discountValue: value })
  )

  const unsaved = manualBasePriceUnsaved || discountValueUnsaved
  useEffect(() => {
    onUnsavedChange?.(unsaved)
  }, [unsaved, onUnsavedChange])
  // Unmounting with nothing left to save: the host must not wait for us.
  useEffect(() => () => onUnsavedChange?.(false), [onUnsavedChange])

  if (deal.vehicleSnapshotFrozenAt == null && deal.vehicleSource !== 'manual') {
    return (
      <Text size="sm" c="dimmed">
        {t('offerWorkspace.pricing.emptyHint')}
      </Text>
    )
  }

  return (
    <Stack gap="xs">
      {deal.vehicleSource === 'manual' ? (
        <NumberInput
          label={t('offerWorkspace.pricing.manualBasePrice')}
          value={manualBasePrice}
          onChange={(v) => setManualBasePrice(v === '' ? '' : Number(v))}
        />
      ) : (
        row(t('offerWorkspace.pricing.basePrice'), deal.basePrice != null ? formatCurrencyChf(Number(deal.basePrice)) : '—')
      )}
      {deal.optionsTotal != null && Number(deal.optionsTotal) > 0 &&
        row(t('offerWorkspace.pricing.optionsTotal'), formatCurrencyChf(Number(deal.optionsTotal)))}
      {row(t('offerWorkspace.pricing.listPrice'), deal.listPrice != null ? formatCurrencyChf(Number(deal.listPrice)) : '—', { bold: true })}
      {deal.accessoriesTotal != null && Number(deal.accessoriesTotal) > 0 &&
        row(t('offerWorkspace.pricing.accessoriesTotal'), formatCurrencyChf(Number(deal.accessoriesTotal)))}

      <Group grow>
        <Select
          label={t('offerWorkspace.pricing.discountType')}
          data={[
            { value: '', label: t('offerWorkspace.pricing.noDiscount') },
            { value: 'percent', label: t('offerWorkspace.pricing.discountPercent') },
            { value: 'amount', label: t('offerWorkspace.pricing.discountAmount') },
          ]}
          value={deal.discountType ?? ''}
          onChange={(v) => onDiscountChange({ discountType: v || null, discountValue: discountValue === '' ? null : discountValue })}
        />
        <NumberInput
          label={t('offerWorkspace.pricing.discountValue')}
          disabled={!deal.discountType}
          value={discountValue}
          onChange={(v) => setDiscountValue(v === '' ? '' : Number(v))}
        />
      </Group>
      {deal.discountAmount != null &&
        Number(deal.discountAmount) > 0 &&
        row(t('offerWorkspace.pricing.discountAmount'), `− ${formatCurrencyChf(Number(deal.discountAmount))}`, { negative: true })}

      {row(t('offerWorkspace.pricing.grossPrice'), deal.grossPrice != null ? formatCurrencyChf(Number(deal.grossPrice)) : '—', { bold: true })}

      {deal.margin != null && (
        <Stack gap={2} mt="xs" p="xs" style={{ border: '1px dashed var(--mantine-color-red-4)', borderRadius: 6 }}>
          <Text size="xs" fw={700} c="red">
            {t('offerWorkspace.pricing.internalOnly')}
          </Text>
          {row(t('offerWorkspace.pricing.costBasis'), deal.costBasis != null ? formatCurrencyChf(Number(deal.costBasis)) : '—')}
          {row(t('offerWorkspace.pricing.margin'), formatCurrencyChf(Number(deal.margin)), { bold: true })}
        </Stack>
      )}
    </Stack>
  )
}
