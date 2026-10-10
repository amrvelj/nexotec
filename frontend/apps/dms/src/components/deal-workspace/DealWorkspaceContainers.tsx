import { useState } from 'react'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import { Alert, Button, Group, Stack, Text, TextInput, UnstyledButton } from '@mantine/core'
import { useDebouncedValue } from '@mantine/hooks'
import { useTranslation } from 'react-i18next'
import { OverviewCard, Picker, SalesStatusBadge, useOverlay, type PickerRow } from '@nexotec/ui-kit'
import { api } from '../../api/client'
import { OfferAccessoriesAndOptions } from '../OfferAccessoriesAndOptions'
import { PriceBuildUp } from '../PriceBuildUp'
import { ValuationSourceMarker } from '../ValuationSourceMarker'
import { ValuationCreateDialog } from '../ValuationCreateDialog'
import { ConfiguratorOverlay } from '../configurator/ConfiguratorOverlay'
import { HostCommitError } from '../configurator/hostCommitError'
import { HostConfigurationCard } from '../configurator/HostConfigurationCard'
import { useIdempotencyKey } from '../../hooks/useIdempotencyKey'
import { formatCurrencyChf } from '../../utils/format'
import type {
  ConfigurationRead,
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
 * `patch` is the host's own autosave PATCH. It must read the deal's
 * current version itself (the hosts keep it in a ref), because the
 * configurator overlay calls it from a closure that can outlive the render
 * it was created in. It rejects when the server refuses the edit. The
 * contract page reports why (confirmed elsewhere, stale version); the offer
 * workspace shows nothing, as before this extraction, when its autosave
 * fails. The fire-and-forget callers here only stop the rejection going
 * unhandled; the configurator turns it into its own error.
 */
export interface DealContainerProps<D extends SalesPricedDealRead> {
  ownerType: SalesDealOwnerType
  deal: D
  /** The newest copy of the deal, for requests sent from a closure. */
  getDeal: () => D | undefined
  patch: (patch: Record<string, unknown>) => Promise<void>
  onDealUpdated: (deal: D) => void
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
  onDealUpdated,
  onOpenStockItem,
}: DealContainerProps<D> & { onOpenStockItem?: () => void }) {
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
      />
      <OfferAccessoriesAndOptions ownerType={ownerType} deal={deal} onDealUpdated={onDealUpdated} />
    </OverviewCard>
  )
}

/** Eintauschfahrzeug — a VIN-identified trade-in, or the valuation path:
 * the configurator in `record` mode, then a valuation the deal references
 * (FR-C-12's carve-out; Path A could never find the customer's own car). */
export function DealTradeInContainer<D extends SalesPricedDealRead>({
  ownerType,
  deal,
  getDeal,
  onDealUpdated,
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
    const current = getDeal()
    if (!current) return
    setTradeInError(null)
    try {
      const path = `${basePath}/trade-in/valuation`
      const body = { valuationId: valuation.id }
      const updated = await api.post<D>(path, body, {
        'If-Match': String(current.version),
        ...valuationKey.headers([path, body]),
      })
      valuationKey.renew()
      onDealUpdated(updated)
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
    const current = getDeal()
    if (!current) return
    const path = `${basePath}/trade-in`
    const updated = await api.post<D>(path, body, {
      'If-Match': String(current.version),
      ...tradeInKey.headers([path, body]),
    })
    tradeInKey.renew()
    onDealUpdated(updated)
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
