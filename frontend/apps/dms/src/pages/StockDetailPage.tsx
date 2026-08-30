import { useState } from 'react'
import { useParams, useSearchParams } from 'react-router-dom'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import { Alert, Loader } from '@mantine/core'
import { FileSignature, Handshake, Warehouse } from 'lucide-react'
import { useTranslation } from 'react-i18next'
import { DetailHeader, DetailTabs, StockConditionBadge, StockLifecycleBadge, StockReservationBadge, useSetBreadcrumb, type DetailTab } from '@nexotec/ui-kit'
import { api, ApiError } from '../api/client'
import { toSwissLocale, type SupportedLanguage } from '../i18n'
import { translatedStockConditionLabel, translatedStockLifecycleLabel, translatedStockReservationLabel } from '../stockOptions'
import { DetailsTab } from '../components/stock-detail/DetailsTab'
import type { StockItemRead } from '../api/types'

const DEFAULT_TAB = 'details'

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
 * reference prototype. Both are disabled-with-reason for now: Sales (WP-8)
 * doesn't exist yet, so there is genuinely nowhere for either action to go.
 */
export function StockDetailContent({ stockItemId: id, embedded = false }: StockDetailContentProps) {
  const { t, i18n } = useTranslation()
  const locale = toSwissLocale(i18n.language as SupportedLanguage)
  const [searchParams, setSearchParams] = useSearchParams()
  const [embeddedTab, setEmbeddedTab] = useState(DEFAULT_TAB)
  const queryClient = useQueryClient()
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

  useSetBreadcrumb(embedded ? null : [t('shell.nav.inventory'), id])

  const itemQuery = useQuery({
    queryKey: ['stock-item', id],
    queryFn: () => api.get<StockItemRead>(`/inventory/stock-items/${id}`),
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

  const recordPurchase = async (data: {
    supplierName: string
    supplierIsVatRegistered: boolean
    purchasePrice: number
    purchaseDate: string
    purchaseInvoiceRef?: string
  }) => {
    const item = itemQuery.data
    if (!item) return
    const updated = await api.post<StockItemRead>(`/inventory/stock-items/${id}/purchase`, data, {
      'If-Match': String(item.version),
    })
    queryClient.setQueryData(['stock-item', id], updated)
  }

  const tabs: DetailTab[] = [{ id: 'details', label: t('stockDetail.tabs.details') }]

  if (itemQuery.isLoading) return <Loader />
  if (itemQuery.isError || !itemQuery.data) {
    return (
      <Alert color="red" title={t('stockDetail.errors.failedToLoad')}>
        {itemQuery.error instanceof ApiError ? itemQuery.error.message : t('stockDetail.errors.somethingWentWrong')}
      </Alert>
    )
  }

  const item = itemQuery.data
  const notYetAvailableReason = t('stockDetail.actions.salesNotYetBuilt')

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 20 }}>
      <DetailHeader
        entityMark={<Warehouse size={24} />}
        title={item.vehicleLabel}
        businessKey={item.vin ? `${item.stockNumber} · ${item.vin}` : item.stockNumber}
        badges={
          <>
            <StockConditionBadge condition={item.condition} label={translatedStockConditionLabel(t, item.condition)} />
            <StockLifecycleBadge status={item.lifecycleStatus} label={translatedStockLifecycleLabel(t, item.lifecycleStatus)} />
            <StockReservationBadge state={item.reservationState} label={translatedStockReservationLabel(t, item.reservationState)} />
          </>
        }
        alternativeAction={{
          label: t('stockDetail.actions.createOffer'),
          icon: <Handshake size={16} />,
          onClick: () => {},
          disabled: true,
          disabledReason: notYetAvailableReason,
        }}
        primaryAction={{
          label: t('stockDetail.actions.createContract'),
          icon: <FileSignature size={16} />,
          onClick: () => {},
          disabled: true,
          disabledReason: notYetAvailableReason,
        }}
      />

      <DetailTabs tabs={tabs} activeTab={activeTab} onTabChange={setActiveTab} />

      {activeTab === 'details' && (
        <DetailsTab item={item} locale={locale} onSaveField={saveField} onReload={reload} onRecordPurchase={recordPurchase} />
      )}
    </div>
  )
}
