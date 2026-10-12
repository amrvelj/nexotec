import { useState } from 'react'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import { Alert, Button, Group, Modal, Stack, Text, TextInput, UnstyledButton } from '@mantine/core'
import { useDebouncedValue } from '@mantine/hooks'
import { useTranslation } from 'react-i18next'
import { OverviewCard, Picker, SalesStatusBadge, useOverlay, type PickerRow } from '@nexotec/ui-kit'
import { api } from '../../api/client'
import { CustomerCreateDialog } from '../CustomerCreateDialog'
import { OfferAccessoriesAndOptions } from '../OfferAccessoriesAndOptions'
import { PriceBuildUp } from '../PriceBuildUp'
import { ValuationSourceMarker } from '../ValuationSourceMarker'
import { ValuationCreateDialog } from '../ValuationCreateDialog'
import { ConfiguratorOverlay } from '../configurator/ConfiguratorOverlay'
import { HostCommitError } from '../configurator/hostCommitError'
import { HostConfigurationCard } from '../configurator/HostConfigurationCard'
import { useIdempotencyKey } from '../../hooks/useIdempotencyKey'
import type { DealSaveQueue } from '../../hooks/useDealSaveQueue'
import { formatCurrencyChf } from '../../utils/format'
import type {
  ConfigurationRead,
  CustomerPage,
  CustomerRead,
  SalesDealOwnerType,
  SalesPricedDealRead,
  StockItemPage,
  StockItemRead,
  ValuationRead,
} from '../../api/types'

/**
 * KAN-67 (ADR-083) — the vehicle, price build-up and trade-in containers of
 * FR-S-05's workspace, shared by the offer draft (OfferWorkspacePage) and a
 * pending contract (ContractDetailPage). The backend runs one edit path for
 * both (app/sales/services/deal_editing.py); these run one screen for both.
 * `ownerType` picks the route; every request body is the same.
 *
 * `patch` is the host's own autosave PATCH, sent through `save`. It rejects
 * when the server refuses the edit, and the host says why in the UI
 * language (confirmed elsewhere, stale version). The fire-and-forget
 * callers here only stop the rejection going unhandled; the configurator
 * turns it into its own error.
 *
 * `save` is the host's save queue (KAN-314, `useDealSaveQueue`): every
 * write that bumps the deal's version — the PATCH, line items, trade-in,
 * trade-in valuation — goes through it, so overlapping saves reach the
 * server one after the other, each with the version the previous one
 * returned, and a host that ends the edit (signing a contract) can wait for
 * them (KAN-288). It reads the deal's current version when the save is
 * sent, never from a render's closure, which is what lets the configurator
 * overlay call it from a closure that outlives the render it was made in.
 */
export interface DealContainerProps<D extends SalesPricedDealRead> {
  ownerType: SalesDealOwnerType
  deal: D
  patch: (patch: Record<string, unknown>) => Promise<void>
  save: DealSaveQueue<D>['save']
}

const routeSegment = (ownerType: SalesDealOwnerType) => (ownerType === 'offer' ? 'offers' : 'contracts')
const reportedByHost = () => undefined

export function RequirementBadge({ requirement }: { requirement: string }) {
  const { t } = useTranslation()
  return (
    <SalesStatusBadge
      status={requirement === 'required' ? 'open' : 'draft'}
      label={requirement === 'required' ? t('offerWorkspace.requirement.required') : t('offerWorkspace.requirement.optional')}
    />
  )
}

function requirementOf(deal: SalesPricedDealRead, id: string, fallback: string) {
  return (deal.containers ?? []).find((c) => c.id === id)?.requirement ?? fallback
}

/** The buyer a deal's customer container shows: the customer record, for its
 * credit-block and do-not-contact warnings. One query key with the hosts,
 * which read the same record to gate their primary action. */
export function useDealCustomer(customerId: string | null | undefined) {
  return useQuery({
    queryKey: ['customer', customerId],
    queryFn: () => api.get<CustomerRead>(`/customers/${customerId}`),
    enabled: customerId != null,
  })
}

/** Kunde — the buyer, picked from the customers or created on the spot.
 * The offer workspace's own container, shared since KAN-307 (Anto,
 * 2026-10-12, amending ADR-083): a pending contract picks its buyer the
 * same way, and cannot be signed without one. `onOpenCustomer` opens the
 * record over the deal (ADR-059), never instead of it. */
export function DealCustomerContainer<D extends SalesPricedDealRead>({
  ownerType,
  deal,
  patch,
  onOpenCustomer,
}: Pick<DealContainerProps<D>, 'ownerType' | 'deal' | 'patch'> & { onOpenCustomer: () => void }) {
  const { t } = useTranslation()
  const selectedCustomer = useDealCustomer(deal.customerId).data ?? null
  const [pickerOpen, setPickerOpen] = useState(false)
  const [createOpen, setCreateOpen] = useState(false)
  const [query, setQuery] = useState('')
  const [debouncedQuery] = useDebouncedValue(query, 250)
  const search = useQuery({
    queryKey: ['customer-search', debouncedQuery],
    queryFn: () => api.get<CustomerPage>(`/customers?q=${encodeURIComponent(debouncedQuery)}&limit=10`),
    enabled: pickerOpen && debouncedQuery.length > 0,
  })
  const choose = (customerId: string) => {
    patch({ customerId }).catch(reportedByHost)
  }

  return (
    <OverviewCard title={t('offerWorkspace.containers.customer')} badge={<RequirementBadge requirement={requirementOf(deal, 'customer', 'required')} />}>
      {deal.customerId ? (
        <Stack gap={4}>
          <UnstyledButton onClick={onOpenCustomer}>
            <Text fw={600} c="purple">{deal.customerLabel}</Text>
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
          <Text size="sm" c="dimmed">
            {ownerType === 'contract' ? t('offerWorkspace.customer.emptyHintContract') : t('offerWorkspace.customer.emptyHint')}
          </Text>
          <Group gap="xs">
            <Button variant="default" size="xs" onClick={() => setPickerOpen(true)}>
              {t('offerWorkspace.customer.search')}
            </Button>
            <Button variant="subtle" size="xs" onClick={() => setCreateOpen(true)}>
              {t('offerWorkspace.customer.createNew')}
            </Button>
          </Group>
        </Stack>
      )}

      <Modal opened={pickerOpen} onClose={() => setPickerOpen(false)} title={t('offerWorkspace.customer.search')}>
        <Picker
          rows={(search.data?.items ?? []).map(
            (c: CustomerRead): PickerRow => ({
              id: c.id,
              identifier: c.customerNumber,
              label: c.companyName ?? [c.firstName, c.lastName].filter(Boolean).join(' '),
            })
          )}
          query={query}
          onQueryChange={setQuery}
          onSelect={(row) => {
            choose(row.id)
            setPickerOpen(false)
          }}
          loading={search.isFetching}
          placeholder={t('offerWorkspace.customer.search')}
        />
      </Modal>

      <CustomerCreateDialog
        opened={createOpen}
        onClose={() => setCreateOpen(false)}
        onCreated={(customer) => {
          choose(customer.id)
          setCreateOpen(false)
        }}
      />
    </OverviewCard>
  )
}

/** Fahrzeug — FR-S-05 Path A (stock search) or Path B (the configurator,
 * `build` mode only, PRD v1.4: a used car being sold comes from stock). */
export function DealVehicleContainer<D extends SalesPricedDealRead>({ ownerType, deal, patch }: DealContainerProps<D>) {
  const { t } = useTranslation()
  const queryClient = useQueryClient()
  const overlay = useOverlay()
  const [vehicleMode, setVehicleMode] = useState<'idle' | 'search'>('idle')
  const [vehicleQuery, setVehicleQuery] = useState('')
  const [debouncedVehicleQuery] = useDebouncedValue(vehicleQuery, 250)
  const vehicleSearch = useQuery({
    queryKey: ['stock-item-search', debouncedVehicleQuery],
    queryFn: () => api.get<StockItemPage>(`/inventory/stock-items?q=${encodeURIComponent(debouncedVehicleQuery)}&limit=10`),
    enabled: vehicleMode === 'search' && debouncedVehicleQuery.length > 0,
  })

  // FR-C-12 — Path B is the configurator, as an overlay (ADR-059): the
  // half-built deal underneath is never navigated away from.
  const openConfigurator = (existing: ConfigurationRead | null = null) =>
    overlay.push({
      key: `configurator-${ownerType}-vehicle`,
      content: (
        <ConfiguratorOverlay
          allowedModes={['build']}
          existing={existing}
          onCommitted={async (configuration) => {
            try {
              await patch({ configurationId: configuration.id })
            } catch {
              throw new HostCommitError(t('offerWorkspace.vehicle.attachError'))
            }
            await queryClient.invalidateQueries({ queryKey: ['configuration', configuration.id] })
            overlay.pop()
          }}
          onClose={() => overlay.pop()}
        />
      ),
    })

  return (
    <OverviewCard
      title={t('offerWorkspace.containers.vehicle')}
      badge={<RequirementBadge requirement={requirementOf(deal, 'vehicle', 'required')} />}
    >
      {deal.configurationId ? (
        <Stack gap={4} data-testid="offer-vehicle-configured">
          <Text size="xs" c="dimmed">{t('offerWorkspace.vehicle.configured')}</Text>
          <HostConfigurationCard configurationId={deal.configurationId} onOpenConfigurator={openConfigurator} />
        </Stack>
      ) : deal.vehicleLabel ? (
        <Text fw={600}>{deal.vehicleLabel}</Text>
      ) : vehicleMode === 'search' ? (
        <Picker
          rows={(vehicleSearch.data?.items ?? []).map(
            (item: StockItemRead): PickerRow => ({ id: item.id, identifier: item.stockNumber, label: item.vehicleLabel, sublabel: item.vin ?? undefined })
          )}
          query={vehicleQuery}
          onQueryChange={setVehicleQuery}
          onSelect={(row) =>
            void patch({ vehicleSource: 'stock', stockItemId: row.id, vehicleLabel: row.label })
              .then(() => setVehicleMode('idle'))
              .catch(reportedByHost)
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
            <Button variant="subtle" size="xs" onClick={() => openConfigurator()}>
              {t('offerWorkspace.vehicle.configure')}
            </Button>
          </Group>
        </Stack>
      )}
    </OverviewCard>
  )
}

/** Preisaufbau — the build-up, the seller's discount and base price, the
 * option and accessory lines. Needs a vehicle first (FR-S-05). */
export function DealPricingContainer<D extends SalesPricedDealRead>({
  ownerType,
  deal,
  patch,
  save,
  onOpenStockItem,
  onUnsavedChange,
}: DealContainerProps<D> & { onOpenStockItem?: () => void; onUnsavedChange?: (unsaved: boolean) => void }) {
  const { t } = useTranslation()
  const pricingStatus = (deal.containers ?? []).find((c) => c.id === 'pricing')?.status
  return (
    <OverviewCard
      title={t('offerWorkspace.containers.pricing')}
      badge={<RequirementBadge requirement={requirementOf(deal, 'pricing', 'required')} />}
    >
      {pricingStatus === 'in_progress' && (
        <Alert color="orange" title={t('offerWorkspace.pricing.noPriceTitle')} py="xs" mb="xs">
          <Stack gap={4}>
            <Text size="sm">{t('offerWorkspace.pricing.noPriceBody')}</Text>
            {deal.stockItemId && onOpenStockItem && (
              <UnstyledButton onClick={onOpenStockItem}>
                <Text size="sm" fw={600} c="purple">{t('offerWorkspace.pricing.noPriceLink')}</Text>
              </UnstyledButton>
            )}
          </Stack>
        </Alert>
      )}
      <PriceBuildUp
        deal={deal}
        onDiscountChange={(change) =>
          void patch({ discountType: change.discountType, discountValue: change.discountValue }).catch(reportedByHost)
        }
        onManualBasePriceChange={(value) => void patch({ manualBasePrice: value }).catch(reportedByHost)}
        onUnsavedChange={onUnsavedChange}
      />
      <OfferAccessoriesAndOptions ownerType={ownerType} deal={deal} save={save} />
    </OverviewCard>
  )
}

/** Eintauschfahrzeug — a VIN-identified trade-in, or the valuation path:
 * the configurator in `record` mode, then a valuation the deal references
 * (FR-C-12's carve-out; Path A could never find the customer's own car). */
export function DealTradeInContainer<D extends SalesPricedDealRead>({
  ownerType,
  deal,
  save,
}: DealContainerProps<D>) {
  const { t } = useTranslation()
  const overlay = useOverlay()
  const basePath = `/sales/${routeSegment(ownerType)}/${deal.id}`
  const [tradeInMode, setTradeInMode] = useState(false)
  const [tradeInVin, setTradeInVin] = useState('')
  const [tradeInLabel, setTradeInLabel] = useState('')
  const [tradeInConfiguration, setTradeInConfiguration] = useState<ConfigurationRead | null>(null)
  const [tradeInError, setTradeInError] = useState<string | null>(null)
  // KAN-266 — one key per kind, passed the request: a retry keeps its key
  // and replays its success; another VIN or valuation gets a new one. Each
  // success renews its kind's key.
  const tradeInKey = useIdempotencyKey()
  const valuationKey = useIdempotencyKey()

  // KAN-101 — the trade-in figure is marked manual where it is (ADR-048).
  const tradeInValuationId = deal.tradeInValuationId ?? null
  const tradeInValuationQuery = useQuery({
    queryKey: ['valuation', tradeInValuationId],
    queryFn: () => api.get<ValuationRead>(`/valuations/${tradeInValuationId}`),
    enabled: tradeInValuationId != null,
  })

  const openTradeInConfigurator = () =>
    overlay.push({
      key: `configurator-${ownerType}-trade-in`,
      content: (
        <ConfiguratorOverlay
          allowedModes={['record']}
          onCommitted={(configuration) => {
            setTradeInConfiguration(configuration)
            overlay.pop()
          }}
          onClose={() => overlay.pop()}
        />
      ),
    })

  const attachTradeInValuation = async (valuation: ValuationRead) => {
    setTradeInError(null)
    try {
      const path = `${basePath}/trade-in/valuation`
      const body = { valuationId: valuation.id }
      const updated = await save((current) =>
        api.post<D>(path, body, {
          'If-Match': String(current.version),
          ...valuationKey.headers([path, body]),
        })
      )
      if (!updated) return
      valuationKey.renew()
      setTradeInConfiguration(null)
    } catch {
      setTradeInError(t('offerWorkspace.tradeIn.attachError'))
    }
  }

  const submitTradeIn = async (body: {
    vin: string | null
    plate: string | null
    canton: string | null
    vehicleLabel: string
    customerId: string | null
  }) => {
    const path = `${basePath}/trade-in`
    const updated = await save((current) =>
      api.post<D>(path, body, {
        'If-Match': String(current.version),
        ...tradeInKey.headers([path, body]),
      })
    )
    if (updated) tradeInKey.renew()
  }

  return (
    <OverviewCard title={t('offerWorkspace.containers.tradeIn')} badge={<RequirementBadge requirement="optional" />}>
      {deal.tradeInVehicleId || deal.tradeInConfigurationId ? (
        <Stack gap={4}>
          <Text fw={600}>{deal.tradeInLabel}</Text>
          {deal.tradeInConfigurationId && <HostConfigurationCard configurationId={deal.tradeInConfigurationId} />}
          {deal.tradeInValue != null && (
            <Group gap="xs">
              <Text size="sm" c="dimmed">
                {t('offerWorkspace.tradeIn.value')}: {formatCurrencyChf(Number(deal.tradeInValue))}
              </Text>
              {tradeInValuationQuery.data && <ValuationSourceMarker source={tradeInValuationQuery.data.source} />}
            </Group>
          )}
          {deal.tradeInValuationId && (
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
          {tradeInError && <Alert color="red" py="xs">{tradeInError}</Alert>}
          <Group gap="xs">
            <Button
              size="xs"
              disabled={!tradeInVin || !tradeInLabel}
              onClick={() => {
                setTradeInError(null)
                submitTradeIn({ vin: tradeInVin, plate: null, canton: null, vehicleLabel: tradeInLabel, customerId: null })
                  .then(() => setTradeInMode(false))
                  .catch(() => setTradeInError(t('offerWorkspace.tradeIn.saveError')))
              }}
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
            {deal.customerId ? t('offerWorkspace.tradeIn.readyHint') : t('offerWorkspace.tradeIn.emptyHint')}
          </Text>
          {tradeInError && <Alert color="red" py="xs">{tradeInError}</Alert>}
          <Group gap="xs">
            <Button variant="default" size="xs" onClick={openTradeInConfigurator}>
              {t('offerWorkspace.tradeIn.valueWithConfigurator')}
            </Button>
            <Button variant="subtle" size="xs" onClick={() => setTradeInMode(true)}>
              {t('offerWorkspace.tradeIn.add')}
            </Button>
          </Group>
        </Stack>
      )}

      {tradeInConfiguration && (
        <ValuationCreateDialog
          key={tradeInConfiguration.id}
          opened
          configuration={tradeInConfiguration}
          onClose={() => setTradeInConfiguration(null)}
          onCreated={(valuation) => void attachTradeInValuation(valuation)}
        />
      )}
    </OverviewCard>
  )
}
