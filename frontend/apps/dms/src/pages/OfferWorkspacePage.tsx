import { useState } from 'react'
import { useNavigate, useParams } from 'react-router-dom'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import { Alert, Button, Group, Loader, Modal, NumberInput, Select, Stack, Text, TextInput, Title, UnstyledButton } from '@mantine/core'
import { useDebouncedValue } from '@mantine/hooks'
import { useTranslation } from 'react-i18next'
import { OverviewCard, Picker, SalesStatusBadge, StickyActionFooter, useOverlay, useSetBreadcrumb, type PickerRow } from '@nexotec/ui-kit'
import { api, ApiError } from '../api/client'
import { CustomerCreateFlow } from '../components/CustomerCreateFlow'
import { OfferAccessoriesAndOptions } from '../components/OfferAccessoriesAndOptions'
import { OfferGenerateReviewModal } from '../components/OfferGenerateReviewModal'
import { PriceBuildUp } from '../components/PriceBuildUp'
import { useDebouncedNumberField } from '../hooks/useDebouncedNumberField'
import { CustomerDetailContent } from './CustomerDetailPage'
import { translatedStockConditionOptions } from '../stockOptions'
import { formatCurrencyChf } from '../utils/format'
import type {
  CustomerPage,
  CustomerRead,
  SalesOfferRead,
  StockItemCondition,
  StockItemPage,
  StockItemRead,
} from '../api/types'

function requirementBadge(t: (key: string) => string, requirement: string) {
  return (
    <SalesStatusBadge
      status={requirement === 'required' ? 'open' : 'draft'}
      label={requirement === 'required' ? t('offerWorkspace.requirement.required') : t('offerWorkspace.requirement.optional')}
    />
  )
}

/**
 * `/sales/offers/:id` while status=draft — the container-based generation
 * workspace (FR-S-05, WP-8 PR-2). Fixed container order
 * Kunde -> Fahrzeug -> Preisaufbau -> Eintauschfahrzeug -> Leasing
 * (confirmed live); any order EXCEPT pricing, which needs a vehicle first.
 * Once the offer leaves draft, OfferDetailContent's own shell takes over
 * (App.tsx routes both to the same URL — this page renders only while
 * editable).
 */
export function OfferWorkspacePage() {
  const { id } = useParams<{ id: string }>()
  if (!id) return null
  return <OfferWorkspaceContent offerId={id} />
}

export function OfferWorkspaceContent({ offerId: id }: { offerId: string }) {
  const { t } = useTranslation()
  const navigate = useNavigate()
  const queryClient = useQueryClient()
  useSetBreadcrumb([t('shell.nav.sales'), t('offerWorkspace.title')])

  const offerQuery = useQuery({
    queryKey: ['sales-offer', id],
    queryFn: () => api.get<SalesOfferRead>(`/sales/offers/${id}`),
    enabled: Boolean(id),
  })

  const overlay = useOverlay()
  const customerId = offerQuery.data?.customerId ?? null
  const selectedCustomerQuery = useQuery({
    queryKey: ['customer', customerId],
    queryFn: () => api.get<CustomerRead>(`/customers/${customerId}`),
    enabled: customerId != null,
  })
  const selectedCustomer = selectedCustomerQuery.data ?? null
  // § ADR-059 — opening the selected customer's own record from inside
  // the offer-generation process is an overlay, never a navigation (the
  // half-built offer must survive it intact).
  const openCustomerOverlay = () => {
    if (!customerId) return
    overlay.push({ key: `customer-overlay-${customerId}`, content: <CustomerDetailContent customerId={customerId} embedded /> })
  }

  const [generateOpen, setGenerateOpen] = useState(false)

  const [customerPickerOpen, setCustomerPickerOpen] = useState(false)
  const [customerCreateOpen, setCustomerCreateOpen] = useState(false)
  const [customerQuery, setCustomerQuery] = useState('')
  const [debouncedCustomerQuery] = useDebouncedValue(customerQuery, 250)
  const customerSearch = useQuery({
    queryKey: ['customer-search', debouncedCustomerQuery],
    queryFn: () => api.get<CustomerPage>(`/customers?q=${encodeURIComponent(debouncedCustomerQuery)}&limit=10`),
    enabled: customerPickerOpen && debouncedCustomerQuery.length > 0,
  })

  const [vehicleMode, setVehicleMode] = useState<'idle' | 'search' | 'manual'>('idle')
  const [vehicleQuery, setVehicleQuery] = useState('')
  const [debouncedVehicleQuery] = useDebouncedValue(vehicleQuery, 250)
  const vehicleSearch = useQuery({
    queryKey: ['stock-item-search', debouncedVehicleQuery],
    queryFn: () => api.get<StockItemPage>(`/inventory/stock-items?q=${encodeURIComponent(debouncedVehicleQuery)}&limit=10`),
    enabled: vehicleMode === 'search' && debouncedVehicleQuery.length > 0,
  })
  const [manualLabel, setManualLabel] = useState('')
  const [manualCondition, setManualCondition] = useState<StockItemCondition>('used')

  const [tradeInMode, setTradeInMode] = useState(false)
  const [tradeInVin, setTradeInVin] = useState('')
  const [tradeInLabel, setTradeInLabel] = useState('')

  const patchOffer = async (patch: Record<string, unknown>) => {
    const offer = offerQuery.data
    if (!offer) return
    const updated = await api.patch<SalesOfferRead>(`/sales/offers/${id}`, patch, { 'If-Match': String(offer.version) })
    queryClient.setQueryData(['sales-offer', id], updated)
  }

  // KAN-8 — same fix as PriceBuildUp.tsx's own fields, same root cause:
  // a NumberInput bound straight to a per-keystroke autosave PATCH races
  // itself and drops digits. Called unconditionally (rules of hooks),
  // safe during the loading state below since `offerQuery.data` is
  // simply undefined until it resolves — see the hook's own docstring.
  const [leasingDownPayment, setLeasingDownPayment] = useDebouncedNumberField(
    offerQuery.data?.leasingDownPayment != null ? Number(offerQuery.data.leasingDownPayment) : null,
    offerQuery.data?.id,
    (value) => patchOffer({ leasingDownPayment: value })
  )
  const [leasingTermMonths, setLeasingTermMonths] = useDebouncedNumberField(
    offerQuery.data?.leasingTermMonths ?? null,
    offerQuery.data?.id,
    (value) => patchOffer({ leasingTermMonths: value })
  )
  const [leasingKmPerYear, setLeasingKmPerYear] = useDebouncedNumberField(
    offerQuery.data?.leasingKmPerYear ?? null,
    offerQuery.data?.id,
    (value) => patchOffer({ leasingKmPerYear: value })
  )

  const submitTradeIn = async (body: {
    vin: string | null
    plate: string | null
    canton: string | null
    vehicleLabel: string
    customerId: string | null
  }) => {
    const offer = offerQuery.data
    if (!offer) return
    const updated = await api.post<SalesOfferRead>(`/sales/offers/${id}/trade-in`, body, {
      'If-Match': String(offer.version),
    })
    queryClient.setQueryData(['sales-offer', id], updated)
  }

  if (offerQuery.isLoading) return <Loader />
  if (offerQuery.isError || !offerQuery.data) {
    return (
      <Alert color="red" title={t('offerDetail.errors.failedToLoad')}>
        {offerQuery.error instanceof ApiError ? offerQuery.error.message : t('offerDetail.errors.somethingWentWrong')}
      </Alert>
    )
  }

  const offer = offerQuery.data
  const containerById = Object.fromEntries((offer.containers ?? []).map((c) => [c.id, c]))
  const missing = (offer.containers ?? []).filter((c) => c.requirement === 'required' && c.status === 'not_started')

  return (
    <Stack gap="lg">
      <Group justify="space-between">
        <Title order={2}>{t('offerWorkspace.title')}</Title>
        <Text c="dimmed">{offer.offerNumber} · {t(`salesEnums.dealStatus.${offer.status}`)}</Text>
      </Group>

      {/* Kunde */}
      <OverviewCard title={t('offerWorkspace.containers.customer')} badge={requirementBadge(t, containerById.customer?.requirement ?? 'required')}>
        {offer.customerId ? (
          <Stack gap={4}>
            <UnstyledButton onClick={openCustomerOverlay}>
              <Text fw={600} c="purple">{offer.customerLabel}</Text>
            </UnstyledButton>
            {selectedCustomer?.creditBlock && (
              <Alert color="orange" title={t('offerWorkspace.customer.creditBlockTitle')} py="xs">
                {t('offerWorkspace.customer.creditBlockBody', { reason: selectedCustomer.creditBlockReason ?? '—' })}
              </Alert>
            )}
            {selectedCustomer?.lifecycleStatus === 'do_not_contact' && (
              <Alert color="red" title={t('offerWorkspace.customer.doNotContactTitle')} py="xs">
                {t('offerWorkspace.customer.doNotContactBody')}
              </Alert>
            )}
          </Stack>
        ) : (
          <Stack gap="xs">
            <Text size="sm" c="dimmed">{t('offerWorkspace.customer.emptyHint')}</Text>
            <Group gap="xs">
              <Button variant="default" size="xs" onClick={() => setCustomerPickerOpen(true)}>
                {t('offerWorkspace.customer.search')}
              </Button>
              <Button variant="subtle" size="xs" onClick={() => setCustomerCreateOpen(true)}>
                {t('offerWorkspace.customer.createNew')}
              </Button>
            </Group>
          </Stack>
        )}
      </OverviewCard>

      {/* Fahrzeug */}
      <OverviewCard title={t('offerWorkspace.containers.vehicle')} badge={requirementBadge(t, containerById.vehicle?.requirement ?? 'required')}>
        {offer.vehicleLabel ? (
          <Text fw={600}>{offer.vehicleLabel}</Text>
        ) : vehicleMode === 'manual' ? (
          <Stack gap="xs">
            <TextInput
              placeholder={t('offerWorkspace.vehicle.manualLabelPlaceholder')}
              value={manualLabel}
              onChange={(e) => setManualLabel(e.currentTarget.value)}
            />
            <Select data={translatedStockConditionOptions(t)} value={manualCondition} onChange={(v) => setManualCondition((v as StockItemCondition) ?? 'used')} />
            <Group gap="xs">
              <Button
                size="xs"
                disabled={!manualLabel}
                onClick={() =>
                  patchOffer({ vehicleSource: 'manual', vehicleLabel: manualLabel, manualVehicleCondition: manualCondition }).then(() =>
                    setVehicleMode('idle')
                  )
                }
              >
                {t('common.save')}
              </Button>
              <Button variant="subtle" size="xs" onClick={() => setVehicleMode('idle')}>
                {t('common.cancel')}
              </Button>
            </Group>
          </Stack>
        ) : vehicleMode === 'search' ? (
          <Picker
            rows={(vehicleSearch.data?.items ?? []).map(
              (item: StockItemRead): PickerRow => ({ id: item.id, identifier: item.stockNumber, label: item.vehicleLabel, sublabel: item.vin ?? undefined })
            )}
            query={vehicleQuery}
            onQueryChange={setVehicleQuery}
            onSelect={(row) =>
              patchOffer({ vehicleSource: 'stock', stockItemId: row.id, vehicleLabel: row.label }).then(() => setVehicleMode('idle'))
            }
            loading={vehicleSearch.isFetching}
            placeholder={t('offerWorkspace.vehicle.searchPlaceholder')}
          />
        ) : (
          <Stack gap="xs">
            <Text size="sm" c="dimmed">{t('offerWorkspace.vehicle.emptyHint')}</Text>
            <Group gap="xs">
              <Button variant="default" size="xs" onClick={() => setVehicleMode('search')}>
                {t('offerWorkspace.vehicle.search')}
              </Button>
              <Button variant="subtle" size="xs" onClick={() => setVehicleMode('manual')}>
                {t('offerWorkspace.vehicle.configure')}
              </Button>
            </Group>
          </Stack>
        )}
      </OverviewCard>

      {/* Preisaufbau */}
      <OverviewCard title={t('offerWorkspace.containers.pricing')}>
        <PriceBuildUp
          offer={offer}
          onDiscountChange={(patch) => patchOffer({ discountType: patch.discountType, discountValue: patch.discountValue })}
          onManualBasePriceChange={(value) => patchOffer({ manualBasePrice: value })}
        />
        <OfferAccessoriesAndOptions
          offer={offer}
          onOfferUpdated={(updated) => queryClient.setQueryData(['sales-offer', id], updated)}
        />
      </OverviewCard>

      {/* Eintauschfahrzeug */}
      <OverviewCard title={t('offerWorkspace.containers.tradeIn')} badge={requirementBadge(t, 'optional')}>
        {offer.tradeInVehicleId ? (
          <Stack gap={4}>
            <Text fw={600}>{offer.tradeInLabel}</Text>
            {offer.tradeInValue != null && (
              <Text size="sm" c="dimmed">
                {t('offerWorkspace.tradeIn.value')}: {formatCurrencyChf(Number(offer.tradeInValue))}
              </Text>
            )}
            {offer.tradeInValuationId && (
              <Text size="xs" c="dimmed">
                {t('offerWorkspace.tradeIn.fromExistingValuation')}
              </Text>
            )}
          </Stack>
        ) : tradeInMode ? (
          <Stack gap="xs">
            <TextInput
              label={t('offerWorkspace.tradeIn.vinLabel')}
              placeholder={t('offerWorkspace.tradeIn.vinPlaceholder')}
              value={tradeInVin}
              onChange={(e) => setTradeInVin(e.currentTarget.value)}
            />
            <TextInput
              label={t('offerWorkspace.tradeIn.labelLabel')}
              value={tradeInLabel}
              onChange={(e) => setTradeInLabel(e.currentTarget.value)}
            />
            <Group gap="xs">
              <Button
                size="xs"
                disabled={!tradeInVin || !tradeInLabel}
                onClick={() =>
                  submitTradeIn({ vin: tradeInVin, plate: null, canton: null, vehicleLabel: tradeInLabel, customerId: null }).then(
                    () => setTradeInMode(false)
                  )
                }
              >
                {t('common.save')}
              </Button>
              <Button variant="subtle" size="xs" onClick={() => setTradeInMode(false)}>
                {t('common.cancel')}
              </Button>
            </Group>
          </Stack>
        ) : (
          <Stack gap="xs">
            <Text size="sm" c="dimmed">
              {offer.customerId ? t('offerWorkspace.tradeIn.readyHint') : t('offerWorkspace.tradeIn.emptyHint')}
            </Text>
            <Button variant="default" size="xs" onClick={() => setTradeInMode(true)} style={{ alignSelf: 'flex-start' }}>
              {t('offerWorkspace.tradeIn.add')}
            </Button>
          </Stack>
        )}
      </OverviewCard>

      {/* Leasing — S-D03: a refused calculator, free-text only */}
      <OverviewCard title={t('offerWorkspace.containers.leasing')} badge={requirementBadge(t, 'optional')}>
        <Stack gap="xs">
          <Text size="sm" c="dimmed">{t('offerWorkspace.leasing.hint')}</Text>
          <Group grow>
            <NumberInput
              label={t('offerWorkspace.leasing.downPayment')}
              value={leasingDownPayment}
              onChange={(v) => setLeasingDownPayment(v === '' ? '' : Number(v))}
            />
            <NumberInput
              label={t('offerWorkspace.leasing.termMonths')}
              value={leasingTermMonths}
              onChange={(v) => setLeasingTermMonths(v === '' ? '' : Number(v))}
            />
            <NumberInput
              label={t('offerWorkspace.leasing.kmPerYear')}
              value={leasingKmPerYear}
              onChange={(v) => setLeasingKmPerYear(v === '' ? '' : Number(v))}
            />
          </Group>
        </Stack>
      </OverviewCard>

      <StickyActionFooter
        missingLabel={
          missing.length > 0
            ? `${t('offerWorkspace.missing')}: ${missing.map((m) => t(`offerWorkspace.containers.${m.id === 'trade_in' ? 'tradeIn' : m.id}`)).join(', ')}`
            : null
        }
        primaryAction={
          <Button
            disabled={missing.length > 0 || selectedCustomer?.lifecycleStatus === 'do_not_contact'}
            onClick={() => setGenerateOpen(true)}
          >
            {t('offerWorkspace.continue')}
          </Button>
        }
      />

      <OfferGenerateReviewModal
        opened={generateOpen}
        onClose={() => setGenerateOpen(false)}
        offer={offer}
        onFinalized={(finalized) => {
          queryClient.setQueryData(['sales-offer', id], finalized)
          setGenerateOpen(false)
          navigate(`/sales/offers/${id}`)
        }}
      />

      <Modal opened={customerPickerOpen} onClose={() => setCustomerPickerOpen(false)} title={t('offerWorkspace.customer.search')}>
        <Picker
          rows={(customerSearch.data?.items ?? []).map(
            (c: CustomerRead): PickerRow => ({
              id: c.id,
              identifier: c.customerNumber,
              label: c.companyName ?? [c.firstName, c.lastName].filter(Boolean).join(' '),
            })
          )}
          query={customerQuery}
          onQueryChange={setCustomerQuery}
          onSelect={(row) => {
            patchOffer({ customerId: row.id })
            setCustomerPickerOpen(false)
          }}
          loading={customerSearch.isFetching}
          placeholder={t('offerWorkspace.customer.search')}
        />
      </Modal>

      <Modal opened={customerCreateOpen} onClose={() => setCustomerCreateOpen(false)} title={t('offerWorkspace.customer.createNew')} size="lg">
        <CustomerCreateFlow
          onSuccess={(customer) => {
            patchOffer({ customerId: customer.id })
            setCustomerCreateOpen(false)
          }}
          onCancel={() => setCustomerCreateOpen(false)}
        />
      </Modal>
    </Stack>
  )
}
