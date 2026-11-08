import { useEffect, useRef, useState } from 'react'
import { useLocation, useNavigate, useParams, useSearchParams } from 'react-router-dom'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import { Alert, Loader } from '@mantine/core'
import { Warehouse } from 'lucide-react'
import { useTranslation } from 'react-i18next'
import { Badge, DetailHeader, DetailTabs, StockConditionBadge, StockLifecycleBadge, StockReservationBadge, useSetBreadcrumb, type DetailTab } from '@nexotec/ui-kit'
import { api } from '../api/client'
import { toSwissLocale, type SupportedLanguage } from '../i18n'
import { translatedStockConditionLabel, translatedStockLifecycleLabel, translatedStockReservationLabel } from '../stockOptions'
import { DetailsTab, type DetailsTabAction } from '../components/stock-detail/DetailsTab'
import { ChangeConditionDialog } from '../components/stock-detail/ChangeConditionDialog'
import { buildStockRowMenu, readStockOpenAction, type StockRowMenuItem } from '../components/stockRowMenu'
import { EvaluationTab } from '../components/stock-detail/EvaluationTab'
import { PublishingTab } from '../components/stock-detail/PublishingTab'
import { WagenbuchTab } from '../components/stock-detail/WagenbuchTab'
import type { LedgerCategory, LedgerEntryPage, SalesContractRead, SalesOfferRead, StockItemRead } from '../api/types'
import { useAuth } from '../auth/AuthContext'
import { useCanWrite } from '../auth/useCanWrite'
import { useIdempotencyKey } from '../hooks/useIdempotencyKey'
import { formatDate } from '../utils/format'
import { apiRefusalText } from '../utils/apiRefusal'

const DEFAULT_TAB = 'details'

/** The menu is built on every render, before the car has loaded too (hooks
 * run before the loading state returns); until then it is built on this and
 * never shown or run. */
const NOT_LOADED: StockRowMenuItem = { purchasePrice: null, leftStockAt: null, reservationState: 'none', configurationId: null }


/** The real route — reads `id` from the URL and renders as the full page. */
export function StockDetailPage() {
  const { id } = useParams<{ id: string }>()
  if (!id) return null
  return <StockDetailContent stockItemId={id} />
}

export interface StockDetailContentProps {
  stockItemId: string
  /** § ADR-059 — true when rendered as `Overlay` content (e.g. from a
   * future Sales screen) rather than the real `/stock/:id` route. Same
   * split as CustomerDetailContent/VehicleDetailPage's own overlay-
   * capable content components.
   */
  embedded?: boolean
}

/**
 * FR-I-07 Stock item detail — the same DetailHeader/DetailTabs shell every
 * other 360 screen uses. ADR-061: primary = "Vertrag erstellen" (Contract),
 * alternative = "Offerte erstellen" (Offer) — confirmed against the live
 * reference prototype. KAN-307 (Anto, 2026-10-12: contract generation is
 * "the same as offer generation"): both start a deal for this car and open
 * it; the buyer is picked there. The offer is POST then PATCH (the same
 * Path A choice the offer workspace's vehicle picker makes); the contract
 * takes the car in its POST (`stockItemId`), since a contract is never
 * created empty (KAN-276).
 *
 * KAN-262 — a car that has left stock (`leftStockAt`, FR-I-12) keeps
 * `lifecycleStatus: in_stock` (ADR-054: `sold` is not a lifecycle value),
 * so the lifecycle and reservation badges would read "An Lager" / "Frei".
 * Such an item is history: one badge says when it left, and both sales
 * actions stay disabled with that as their reason (ADR-061: shown and
 * explained, never hidden).
 */
export function StockDetailContent({ stockItemId: id, embedded = false }: StockDetailContentProps) {
  const { t, i18n } = useTranslation()
  const locale = toSwissLocale(i18n.language as SupportedLanguage)
  const [searchParams, setSearchParams] = useSearchParams()
  const [embeddedTab, setEmbeddedTab] = useState(DEFAULT_TAB)
  const queryClient = useQueryClient()
  // KAN-430 (Anto, option A): what the user's role may never use is hidden.
  const canBookPurchase = useCanWrite('stock_acquisition')
  const canPublish = useCanWrite('marketplace_publishing')
  const canWriteStockItems = useCanWrite('stock_items')
  const canWriteConfigurations = useCanWrite('configurations')
  const canChangeCondition = useCanWrite('stock_condition')
  const canCreateContract = useCanWrite('sales_contracts')
  const canCreateOffer = useCanWrite('sales_offers')
  // KAN-211 (ADR-123): «Neu laden» of the car's catalogue details is the
  // dealer manager's (or platform staff's) — not a matrix row, a role.
  const sessionUser = useAuth().user
  const canReloadCatalogueDetails =
    sessionUser?.isDealerManager === true || (sessionUser?.accessRoles ?? []).includes('platform_admin')
  const activeTab = embedded ? embeddedTab : (searchParams.get('tab') ?? DEFAULT_TAB)

  const setActiveTab = (tab: string) => {
    if (embedded) {
      setEmbeddedTab(tab)
      return
    }
    setSearchParams(
      (prev) => {
        const next = new URLSearchParams(prev)
        if (tab === DEFAULT_TAB) next.delete('tab')
        else next.set('tab', tab)
        return next
      },
      { replace: true }
    )
  }

  const itemQuery = useQuery({
    queryKey: ['stock-item', id],
    queryFn: () => api.get<StockItemRead>(`/inventory/stock-items/${id}`),
    enabled: Boolean(id),
  })

  // KAN-63 — the breadcrumb must show the same human-readable label the
  // header's own <DetailHeader title> already renders (vehicleLabel),
  // never the raw route param — that used to leak the UUIDv7 primary key
  // into the most prominent chrome on the page.
  useSetBreadcrumb(
    embedded ? null : [{ label: t('shell.nav.inventory'), href: '/stock' }, itemQuery.data?.vehicleLabel ?? t('stockDetail.header.stockFallback')]
  )
  const ledgerQuery = useQuery({
    queryKey: ['stock-item', id, 'ledger-entries'],
    queryFn: () => api.get<LedgerEntryPage>(`/inventory/stock-items/${id}/ledger-entries`),
    enabled: Boolean(id),
  })

  const saveField = async (patch: Record<string, unknown>) => {
    const item = itemQuery.data
    if (!item) return
    const updated = await api.patch<StockItemRead>(`/inventory/stock-items/${id}`, patch, {
      'If-Match': String(item.version),
    })
    queryClient.setQueryData(['stock-item', id], updated)
  }

  const reload = () => void itemQuery.refetch()

  // KAN-266: one Idempotency-Key per submission of each POST — a retry
  // after a failed save reuses it (the server replays instead of writing
  // twice); a different request gets a new one, and so does every request
  // after a successful one.
  const purchaseKey = useIdempotencyKey()
  const costKey = useIdempotencyKey()
  const offerCreateKey = useIdempotencyKey()
  const contractCreateKey = useIdempotencyKey()
  const navigate = useNavigate()
  const [creating, setCreating] = useState<'offer' | 'contract' | null>(null)
  const [createError, setCreateError] = useState<string | null>(null)
  // KAN-571 — the row menu's actions: those whose dialog lives on the
  // Details tab go there; «Zustand ändern» has its own dialog here.
  const [detailsAction, setDetailsAction] = useState<DetailsTabAction | null>(null)
  const [conditionOpen, setConditionOpen] = useState(false)

  // KAN-307 — "Offerte erstellen": a bare offer, then the same PATCH the
  // workspace's stock picker sends. A retry after a failed POST replays the
  // offer the first POST created (the key), so no second offer appears.
  const createOfferForCar = async () => {
    const item = itemQuery.data
    if (!item) return
    setCreating('offer')
    setCreateError(null)
    try {
      const created = await api.post<SalesOfferRead>('/sales/offers', undefined, offerCreateKey.headers([id]))
      const updated = await api.patch<SalesOfferRead>(
        `/sales/offers/${created.id}`,
        { vehicleSource: 'stock', stockItemId: id, vehicleLabel: item.vehicleLabel },
        { 'If-Match': String(created.version) }
      )
      offerCreateKey.renew()
      navigate(`/sales/offers/${updated.id}`)
    } catch (err) {
      // ADR-035: said, not dropped; the retry carries the key.
      setCreateError(apiRefusalText(t, err, 'stockDetail.errors.createOfferFailed'))
    } finally {
      setCreating(null)
    }
  }

  // KAN-307 — "Vertrag erstellen": one POST that carries the car.
  const createContractForCar = async () => {
    const item = itemQuery.data
    if (!item) return
    setCreating('contract')
    setCreateError(null)
    try {
      const body = { stockItemId: id, vehicleLabel: item.vehicleLabel }
      const created = await api.post<SalesContractRead>('/sales/contracts', body, contractCreateKey.headers(body))
      contractCreateKey.renew()
      navigate(`/sales/contracts/${created.id}`)
    } catch (err) {
      setCreateError(apiRefusalText(t, err, 'stockDetail.errors.createContractFailed'))
    } finally {
      setCreating(null)
    }
  }

  const recordPurchase = async (data: {
    supplierName: string
    supplierIsVatRegistered: boolean
    purchasePrice: number
    purchaseDate: string
    purchaseInvoiceRef?: string
    vin?: string
  }) => {
    const item = itemQuery.data
    if (!item) return
    const path = `/inventory/stock-items/${id}/purchase`
    const updated = await api.post<StockItemRead>(path, data, {
      'If-Match': String(item.version),
      ...purchaseKey.headers([path, data]),
    })
    purchaseKey.renew()
    queryClient.setQueryData(['stock-item', id], updated)
    // KAN-146: the booking posts the Einstandspreis to the Wagenbuch.
    void queryClient.invalidateQueries({ queryKey: ['stock-item', id, 'ledger-entries'] })
  }

  // KAN-146: «Einkauf stornieren» answered with the item back in pipeline;
  // its Wagenbuch gained the counter-entry.
  const purchaseStornoed = (updated: StockItemRead) => {
    queryClient.setQueryData(['stock-item', id], updated)
    void queryClient.invalidateQueries({ queryKey: ['stock-item', id, 'ledger-entries'] })
  }

  const recordCost = async (data: { category: LedgerCategory; amount: number; occurredAt: string; sourceRef: string }) => {
    const path = `/inventory/stock-items/${id}/ledger-entries`
    await api.post(path, data, costKey.headers([path, data]))
    costKey.renew()
    void queryClient.invalidateQueries({ queryKey: ['stock-item', id, 'ledger-entries'] })
  }

  const onDetails = (action: DetailsTabAction) => () => {
    setActiveTab(DEFAULT_TAB)
    setDetailsAction(action)
  }

  // KAN-262 — a car that has left stock can start neither, and says why
  // (the builder puts the reason on every action that car no longer allows).
  // KAN-571 — header and overflow render from the one stock row menu.
  const rowMenu = buildStockRowMenu(
    t,
    itemQuery.data ?? NOT_LOADED,
    {
      createContract: () => void createContractForCar(),
      createOffer: () => void createOfferForCar(),
      recordPurchase: onDetails('recordPurchase'),
      stornoPurchase: onDetails('stornoPurchase'),
      changeCondition: () => setConditionOpen(true),
      attachConfiguration: onDetails('attachConfiguration'),
      editConfiguration: onDetails('editConfiguration'),
    },
    {
      canCreateContract,
      canCreateOffer,
      canBookPurchase,
      canChangeCondition,
      canAttachConfiguration: canWriteStockItems && canWriteConfigurations,
      canEditConfiguration: canWriteConfigurations,
    },
    itemQuery.data?.leftStockAt ? formatDate(itemQuery.data.leftStockAt, locale) : null,
    creating !== null,
  )

  // KAN-571 — the Stock list's row menu opens the car at the action picked
  // there (router state, never the URL). It runs once the car has loaded,
  // exactly as the header's would, and the state is dropped at once, so a
  // reload, Back or Forward never runs it again.
  // Each navigation's action runs at most once: React's StrictMode runs an
  // effect twice in development, and a second «Vertrag erstellen» would be a
  // second contract (the key on a navigation is unique).
  const location = useLocation()
  const openAction = embedded ? null : readStockOpenAction(location.state)
  const handledNavigation = useRef<string | null>(null)
  // The menu is judged with the session's permissions, so it waits for them:
  // before the session is read every role-gated action looks hidden.
  const sessionLoading = useAuth().loading
  const menuReady = Boolean(itemQuery.data) && !sessionLoading
  useEffect(() => {
    if (!openAction || !menuReady || handledNavigation.current === location.key) return
    handledNavigation.current = location.key
    navigate({ pathname: location.pathname, search: location.search, hash: location.hash }, { replace: true, state: null })
    // The menu offers only what this role and this car allow; a router state
    // made by hand asks for nothing more (the server refuses it anyway).
    const entry = rowMenu.offered[openAction]
    if (!entry || entry.disabled) return
    entry.onClick()
    // The menu is rebuilt on every render; the action runs once per
    // navigation that carries it.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [openAction, menuReady])

  const ledgerEntries = ledgerQuery.data?.items ?? []
  const tabs: DetailTab[] = [
    { id: 'details', label: t('stockDetail.tabs.details') },
    { id: 'publishing', label: t('stockDetail.tabs.publishing') },
    { id: 'wagenbuch', label: t('stockDetail.tabs.wagenbuch'), count: ledgerEntries.length },
    { id: 'evaluation', label: t('stockDetail.tabs.evaluation') },
  ]

  if (itemQuery.isLoading) return <Loader />
  if (itemQuery.isError || !itemQuery.data) {
    return (
      <Alert color="red" title={t('stockDetail.errors.failedToLoad')}>
        {apiRefusalText(t, itemQuery.error, 'stockDetail.errors.somethingWentWrong')}
      </Alert>
    )
  }

  const item = itemQuery.data
  const leftStockDate = item.leftStockAt ? formatDate(item.leftStockAt, locale) : null

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 20 }}>
      <DetailHeader
        labels={{ moreActions: t('common.moreActions') }}
        entityMark={<Warehouse size={24} />}
        title={item.vehicleLabel}
        businessKey={item.vin ? `${item.stockNumber} · ${item.vin}` : item.stockNumber}
        badges={
          <>
            <StockConditionBadge condition={item.condition} label={translatedStockConditionLabel(t, item.condition)} />
            {leftStockDate ? (
              <Badge tone="slate" dot>
                {t('stockDetail.leftStock.badge', { date: leftStockDate })}
              </Badge>
            ) : (
              <>
                <StockLifecycleBadge status={item.lifecycleStatus} label={translatedStockLifecycleLabel(t, item.lifecycleStatus)} />
                <StockReservationBadge state={item.reservationState} label={translatedStockReservationLabel(t, item.reservationState)} />
              </>
            )}
          </>
        }
        alternativeAction={rowMenu.alternative}
        primaryAction={rowMenu.primary}
        overflowActions={rowMenu.overflow}
      />

      {createError && (
        <Alert color="red" withCloseButton closeButtonLabel={t('common.dismiss')} onClose={() => setCreateError(null)}>
          {createError}
        </Alert>
      )}

      <DetailTabs tabs={tabs} activeTab={activeTab} onTabChange={setActiveTab} />

      {activeTab === 'details' && (
        <DetailsTab
          item={item}
          locale={locale}
          onSaveField={saveField}
          onReload={reload}
          canRecordPurchase={canBookPurchase}
          canAttachConfiguration={canWriteStockItems && canWriteConfigurations}
          canEditConfiguration={canWriteConfigurations}
          canReloadCatalogueDetails={canReloadCatalogueDetails}
          onRecordPurchase={recordPurchase}
          onPurchaseStornoed={purchaseStornoed}
          requestedAction={detailsAction}
          onRequestedActionHandled={() => setDetailsAction(null)}
        />
      )}
      <ChangeConditionDialog
        opened={conditionOpen}
        onClose={() => setConditionOpen(false)}
        item={item}
        onChanged={(updated) => queryClient.setQueryData(['stock-item', id], updated)}
      />
      {activeTab === 'publishing' && <PublishingTab stockItemId={item.id} locale={locale} canPublish={canPublish} />}
      {activeTab === 'wagenbuch' && (
        <WagenbuchTab
          entries={ledgerEntries}
          locale={locale}
          onRecordCost={recordCost}
          leftStockDate={leftStockDate}
          canRecordCost={canBookPurchase}
        />
      )}
      {activeTab === 'evaluation' && <EvaluationTab stockItemId={item.id} locale={locale} />}
    </div>
  )
}
