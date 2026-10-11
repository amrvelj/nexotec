import { useState } from 'react'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import { Button, Checkbox, Group, NumberInput, Stack, Text, TextInput, UnstyledButton } from '@mantine/core'
import { Trash2 } from 'lucide-react'
import { useTranslation } from 'react-i18next'
import { api, ApiError } from '../api/client'
import { formatCurrencyChf } from '../utils/format'
import type {
  CapabilityCheckRead,
  SalesDealOwnerType,
  SalesLineItemPage,
  SalesLineItemRead,
  SalesPricedDealRead,
} from '../api/types'

export interface OfferAccessoriesAndOptionsProps<D extends SalesPricedDealRead> {
  /** KAN-67 — an offer draft or a pending contract; `ownerType` picks the
   * route (`/sales/offers/…` or `/sales/contracts/…`), same request body. */
  ownerType: SalesDealOwnerType
  deal: D
  onDealUpdated: (deal: D) => void
}

/**
 * WP-8 PR-8 (S-D14) — factory options (frozen at snapshot time, "system-
 * managed") are individually deselectable; accessories are a real offer-
 * level collection the seller builds up directly. Both share one PUT body
 * (app.sales.services.line_items's own idiom) — every mutation here
 * resubmits the FULL current picture rather than a single-item PATCH.
 *
 * Per-line discounts are suppressed by default on a used vehicle
 * (services/line_items.py's own module docstring) — the reason field only
 * appears once a discount is actually requested there, and only on a used
 * vehicle; a new/tagesz/demo vehicle never needs one. Factory options
 * never carry a per-line discount here at all: Stock itself refuses to
 * itemise options on used stock in the first place, so the only vehicles
 * that ever HAVE factory-option rows are exactly the ones the discount-
 * suppression rule doesn't apply to.
 */
export function OfferAccessoriesAndOptions<D extends SalesPricedDealRead>({
  ownerType,
  deal,
  onDealUpdated,
}: OfferAccessoriesAndOptionsProps<D>) {
  const { t } = useTranslation()
  const queryClient = useQueryClient()
  const [newCode, setNewCode] = useState('')
  const [newLabel, setNewLabel] = useState('')
  const [newPrice, setNewPrice] = useState<number | ''>('')
  const [newQuantity, setNewQuantity] = useState<number | ''>(1)
  const [newDiscount, setNewDiscount] = useState<number | ''>('')
  const [newReason, setNewReason] = useState('')
  const [error, setError] = useState<string | null>(null)

  const isUsed = deal.vehicleCondition === 'used'

  // WP-6 PR-5 — without the provider's `packages` capability, factory
  // options already render as this flat, ungrouped checkbox list (no
  // package-grouping UI exists here at all); the banner just explains why
  // to a seller who might otherwise expect option bundles.
  const packagesCapability = useQuery({
    queryKey: ['capability-check', 'packages'],
    queryFn: () => api.get<CapabilityCheckRead>('/integrations/capabilities/packages'),
  })
  const packagesUnavailable = packagesCapability.data?.granted === false

  const lineItemsPath = `/sales/${ownerType === 'offer' ? 'offers' : 'contracts'}/${deal.id}/line-items`
  const lineItemsKey = ['sales-line-items', ownerType, deal.id]
  const lineItemsQuery = useQuery({
    queryKey: lineItemsKey,
    queryFn: () => api.get<SalesLineItemPage>(lineItemsPath),
    enabled: deal.vehicleSnapshotFrozenAt != null || deal.vehicleSource === 'manual',
  })

  const items = lineItemsQuery.data?.items ?? []
  const factoryOptions = items.filter((i) => i.kind === 'factory_option')
  const accessories = items.filter((i) => i.kind === 'accessory')

  const submit = async (body: {
    accessories: Array<Record<string, unknown>>
    factoryOptions?: Array<Record<string, unknown>>
  }) => {
    setError(null)
    try {
      const updated = await api.put<D>(lineItemsPath, body, { 'If-Match': String(deal.version) })
      onDealUpdated(updated)
      await queryClient.invalidateQueries({ queryKey: lineItemsKey })
    } catch (err) {
      // KAN-67 — a contract that is no longer editable is said in the
      // user's language; other refusals keep the server's explanation (a
      // used car's per-line discount needs a reason, …), as before.
      const reason = err instanceof ApiError ? (err.details as { reason?: unknown } | null)?.reason : null
      if (reason === 'contract_not_pending') setError(t('contractDetail.errors.editRefused.notPending'))
      else if (reason === 'contract_priced_without_inputs') setError(t('contractDetail.edit.pricedWithoutInputs'))
      else setError(err instanceof ApiError ? err.message : t('offerWorkspace.lineItems.error'))
    }
  }

  const accessoryPayload = (rows: SalesLineItemRead[]) =>
    rows.map((r) => ({
      id: r.id,
      code: r.code,
      label: r.label,
      unitPrice: r.unitPrice,
      quantity: r.quantity,
      discountType: r.discountType,
      discountValue: r.discountValue,
      discountSuppressedReason: r.discountSuppressedReason,
    }))

  const toggleOption = (option: SalesLineItemRead, included: boolean) => {
    void submit({
      accessories: accessoryPayload(accessories),
      factoryOptions: [{ id: option.id, included }],
    })
  }

  const removeAccessory = (id: string) => {
    void submit({ accessories: accessoryPayload(accessories.filter((a) => a.id !== id)) })
  }

  const addAccessory = () => {
    if (!newLabel || newPrice === '') return
    const wantsDiscount = newDiscount !== '' && Number(newDiscount) > 0
    void submit({
      accessories: [
        ...accessoryPayload(accessories),
        {
          code: newCode || newLabel.slice(0, 16).toUpperCase(),
          label: newLabel,
          unitPrice: String(newPrice),
          quantity: Number(newQuantity || 1),
          discountType: wantsDiscount ? 'amount' : null,
          discountValue: wantsDiscount ? String(newDiscount) : null,
          discountSuppressedReason: wantsDiscount && isUsed ? newReason || null : null,
        },
      ],
    }).then(() => {
      setNewCode('')
      setNewLabel('')
      setNewPrice('')
      setNewQuantity(1)
      setNewDiscount('')
      setNewReason('')
    })
  }

  if (deal.vehicleSnapshotFrozenAt == null && deal.vehicleSource !== 'manual') return null

  return (
    <Stack gap="sm" mt="sm" pt="sm" style={{ borderTop: '1px solid var(--mantine-color-gray-3)' }}>
      {factoryOptions.length > 0 && (
        <Stack gap={4}>
          <Text size="sm" fw={600}>{t('offerWorkspace.lineItems.factoryOptionsTitle')}</Text>
          {packagesUnavailable && (
            <Text size="xs" c="dimmed">{t('offerWorkspace.lineItems.noPackagesCapability')}</Text>
          )}
          {factoryOptions.map((option) => (
            <Group key={option.id} justify="space-between">
              <Checkbox
                label={option.label}
                checked={option.included}
                onChange={(e) => toggleOption(option, e.currentTarget.checked)}
              />
              <Text size="sm" c={option.included ? undefined : 'dimmed'} td={option.included ? undefined : 'line-through'}>
                {formatCurrencyChf(Number(option.unitPrice))}
              </Text>
            </Group>
          ))}
        </Stack>
      )}

      <Stack gap={4}>
        <Text size="sm" fw={600}>{t('offerWorkspace.lineItems.accessoriesTitle')}</Text>
        {accessories.map((a) => (
          <Group key={a.id} justify="space-between">
            <Text size="sm">{a.label}</Text>
            <Group gap="xs">
              <Text size="sm">{formatCurrencyChf(Number(a.unitPrice) * a.quantity)}</Text>
              <UnstyledButton onClick={() => removeAccessory(a.id)} aria-label={t('common.remove')}>
                <Trash2 size={14} />
              </UnstyledButton>
            </Group>
          </Group>
        ))}

        {error && <Text size="xs" c="red">{error}</Text>}

        <Group gap="xs" align="flex-end" wrap="wrap">
          <TextInput
            size="xs"
            label={t('offerWorkspace.lineItems.accessoryLabel')}
            value={newLabel}
            onChange={(e) => setNewLabel(e.currentTarget.value)}
            style={{ flex: 1, minWidth: 140 }}
          />
          <NumberInput
            size="xs"
            label={t('offerWorkspace.lineItems.accessoryPrice')}
            value={newPrice}
            onChange={(v) => setNewPrice(v === '' ? '' : Number(v))}
            style={{ width: 110 }}
          />
          <NumberInput
            size="xs"
            label={t('offerWorkspace.lineItems.accessoryQuantity')}
            value={newQuantity}
            onChange={(v) => setNewQuantity(v === '' ? '' : Number(v))}
            style={{ width: 80 }}
          />
          <NumberInput
            size="xs"
            label={t('offerWorkspace.lineItems.accessoryDiscount')}
            value={newDiscount}
            onChange={(v) => setNewDiscount(v === '' ? '' : Number(v))}
            style={{ width: 110 }}
          />
          <Button size="xs" onClick={addAccessory} disabled={!newLabel || newPrice === ''}>
            {t('offerWorkspace.lineItems.add')}
          </Button>
        </Group>
        {isUsed && newDiscount !== '' && Number(newDiscount) > 0 && (
          <TextInput
            size="xs"
            label={t('offerWorkspace.lineItems.suppressedReason')}
            description={t('offerWorkspace.lineItems.suppressedReasonHint')}
            value={newReason}
            onChange={(e) => setNewReason(e.currentTarget.value)}
          />
        )}
      </Stack>
    </Stack>
  )
}
