import { Group, NumberInput, Select, Stack, Text } from '@mantine/core'
import { useTranslation } from 'react-i18next'
import { useDebouncedNumberField } from '../hooks/useDebouncedNumberField'
import { formatCurrencyChf } from '../utils/format'
import type { SalesOfferRead } from '../api/types'

export interface PriceBuildUpProps {
  offer: SalesOfferRead
  onDiscountChange: (patch: { discountType: string | null; discountValue: number | null }) => void
  onManualBasePriceChange?: (value: number | null) => void
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
export function PriceBuildUp({ offer, onDiscountChange, onManualBasePriceChange }: PriceBuildUpProps) {
  const { t } = useTranslation()

  // KAN-8 — local, debounced state; see useDebouncedNumberField's own
  // docstring for why a NumberInput bound straight to a per-keystroke
  // autosave silently drops digits.
  const [manualBasePrice, setManualBasePrice] = useDebouncedNumberField(
    offer.manualBasePrice != null ? Number(offer.manualBasePrice) : null,
    offer.id,
    (value) => onManualBasePriceChange?.(value)
  )
  const [discountValue, setDiscountValue] = useDebouncedNumberField(
    offer.discountValue != null ? Number(offer.discountValue) : null,
    offer.id,
    (value) => onDiscountChange({ discountType: offer.discountType, discountValue: value })
  )

  if (offer.vehicleSnapshotFrozenAt == null && offer.vehicleSource !== 'manual') {
    return (
      <Text size="sm" c="dimmed">
        {t('offerWorkspace.pricing.emptyHint')}
      </Text>
    )
  }

  return (
    <Stack gap="xs">
      {offer.vehicleSource === 'manual' ? (
        <NumberInput
          label={t('offerWorkspace.pricing.manualBasePrice')}
          value={manualBasePrice}
          onChange={(v) => setManualBasePrice(v === '' ? '' : Number(v))}
        />
      ) : (
        row(t('offerWorkspace.pricing.basePrice'), offer.basePrice != null ? formatCurrencyChf(Number(offer.basePrice)) : '—')
      )}
      {offer.optionsTotal != null && Number(offer.optionsTotal) > 0 &&
        row(t('offerWorkspace.pricing.optionsTotal'), formatCurrencyChf(Number(offer.optionsTotal)))}
      {row(t('offerWorkspace.pricing.listPrice'), offer.listPrice != null ? formatCurrencyChf(Number(offer.listPrice)) : '—', { bold: true })}
      {offer.accessoriesTotal != null && Number(offer.accessoriesTotal) > 0 &&
        row(t('offerWorkspace.pricing.accessoriesTotal'), formatCurrencyChf(Number(offer.accessoriesTotal)))}

      <Group grow>
        <Select
          label={t('offerWorkspace.pricing.discountType')}
          data={[
            { value: '', label: t('offerWorkspace.pricing.noDiscount') },
            { value: 'percent', label: t('offerWorkspace.pricing.discountPercent') },
            { value: 'amount', label: t('offerWorkspace.pricing.discountAmount') },
          ]}
          value={offer.discountType ?? ''}
          onChange={(v) => onDiscountChange({ discountType: v || null, discountValue: discountValue === '' ? null : discountValue })}
        />
        <NumberInput
          label={t('offerWorkspace.pricing.discountValue')}
          disabled={!offer.discountType}
          value={discountValue}
          onChange={(v) => setDiscountValue(v === '' ? '' : Number(v))}
        />
      </Group>
      {offer.discountAmount != null &&
        Number(offer.discountAmount) > 0 &&
        row(t('offerWorkspace.pricing.discountAmount'), `− ${formatCurrencyChf(Number(offer.discountAmount))}`, { negative: true })}

      {row(t('offerWorkspace.pricing.grossPrice'), offer.grossPrice != null ? formatCurrencyChf(Number(offer.grossPrice)) : '—', { bold: true })}

      {offer.margin != null && (
        <Stack gap={2} mt="xs" p="xs" style={{ border: '1px dashed var(--mantine-color-red-4)', borderRadius: 6 }}>
          <Text size="xs" fw={700} c="red">
            {t('offerWorkspace.pricing.internalOnly')}
          </Text>
          {row(t('offerWorkspace.pricing.costBasis'), offer.costBasis != null ? formatCurrencyChf(Number(offer.costBasis)) : '—')}
          {row(t('offerWorkspace.pricing.margin'), formatCurrencyChf(Number(offer.margin)), { bold: true })}
        </Stack>
      )}
    </Stack>
  )
}
