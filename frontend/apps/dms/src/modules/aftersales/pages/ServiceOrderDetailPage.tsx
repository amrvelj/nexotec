import { useState } from 'react'
import { useNavigate, useParams, useSearchParams } from 'react-router-dom'
import { Alert, Loader, Stack } from '@mantine/core'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import { Ban, Plus, Wrench } from 'lucide-react'
import { useTranslation } from 'react-i18next'
import { DetailHeader, DetailTabs, useSetBreadcrumb } from '@nexotec/ui-kit'
import { api } from '../../../api/client'
import type { ServiceOrderJobCreate, ServiceOrderRead } from '../../../api/types'
import { useCanWrite } from '../../../auth/useCanWrite'
import { useIdempotencyKey } from '../../../hooks/useIdempotencyKey'
import { toSwissLocale, type SupportedLanguage } from '../../../i18n'
import { apiRefusalText } from '../../../utils/apiRefusal'
import { formatDateTime } from '../../../utils/format'
import { CancelServiceOrderDialog } from '../components/CancelServiceOrderDialog'
import { HistoryTab } from '../components/order-detail/HistoryTab'
import { JobNameDialog } from '../components/order-detail/OrderDialogs'
import { OverviewTab } from '../components/order-detail/OverviewTab'
import { PositionsTab } from '../components/order-detail/PositionsTab'
import { OrderStateBadge } from '../components/OrderStateBadge'
import { buildServiceOrderRowMenu } from '../components/serviceOrderRowMenu'
import { OPEN_STATES } from '../serviceOrderOptions'

type Tab = 'positions' | 'overview' | 'history'
const TABS: Tab[] = ['positions', 'overview', 'history']

/**
 * D3 — the workshop order (KAN-810 criterion 6): the kit's DetailHeader
 * («<vehicle> · <ordering party>», the number, the state), one primary —
 * «Gruppe hinzufügen» (a position belongs to a job, so «Position» sits on each
 * job) — and the overflow carrying the order's row menu (ADR-061; here
 * «Auftrag stornieren»). Tabs: Positionen, Übersicht, Verlauf (the tab is in
 * the URL). Every change is sent with the order's version (If-Match) and
 * answers the order as it is now; a stale version offers «Neu laden». An
 * action the role may not write is hidden (ADR-104); one the order's state
 * forbids is disabled with its reason. The rules are the server's.
 */
export function ServiceOrderDetailPage() {
  const { id = '' } = useParams<{ id: string }>()
  const { t, i18n } = useTranslation()
  const locale = toSwissLocale(i18n.language as SupportedLanguage)
  const navigate = useNavigate()
  const queryClient = useQueryClient()
  const canWrite = useCanWrite('service_orders')
  const idempotency = useIdempotencyKey()
  const [searchParams, setSearchParams] = useSearchParams()
  const tabParam = searchParams.get('tab') as Tab | null
  const tab: Tab = tabParam && TABS.includes(tabParam) ? tabParam : 'positions'
  const [addJobOpen, setAddJobOpen] = useState(false)
  const [cancelOpen, setCancelOpen] = useState(false)

  const orderQuery = useQuery({
    queryKey: ['service-order', id],
    queryFn: () => api.get<ServiceOrderRead>(`/service-orders/${id}`),
    enabled: id !== '',
  })
  const order = orderQuery.data

  useSetBreadcrumb([{ label: t('shell.nav.orders'), href: '/aftersales' }, order?.orderNumber ?? id])

  const setOrder = (updated: ServiceOrderRead) => {
    queryClient.setQueryData(['service-order', id], updated)
    void queryClient.invalidateQueries({ queryKey: ['service-orders'] })
  }
  const reload = () => void orderQuery.refetch()

  if (orderQuery.isLoading) return <Loader />
  if (orderQuery.isError || !order) {
    return (
      <Alert color="red" title={t('serviceOrder.loadError')}>
        {apiRefusalText(t, orderQuery.error, 'serviceOrder.loadError')}
      </Alert>
    )
  }

  const frozen = !OPEN_STATES.has(order.state)
  const menu = buildServiceOrderRowMenu(t, order, { onCancel: canWrite ? () => setCancelOpen(true) : undefined })
  const cancellation = order.cancellation

  return (
    <Stack gap="lg">
      <DetailHeader
        labels={{ moreActions: t('common.moreActions') }}
        entityMark={<Wrench size={24} />}
        title={`${order.vehicleLabel} · ${order.contact.label}`}
        businessKey={order.orderNumber}
        badges={<OrderStateBadge state={order.state} />}
        primaryAction={
          canWrite
            ? {
                label: t('serviceOrder.actions.addJob'),
                icon: <Plus size={16} />,
                onClick: () => setAddJobOpen(true),
                disabled: frozen,
                disabledReason: frozen ? t('serviceOrder.actions.frozen') : undefined,
              }
            : undefined
        }
        overflowActions={menu.destructive ? menu : undefined}
      />

      {cancellation && (
        <Alert color="red" icon={<Ban size={18} />} title={t('serviceOrder.cancel.bannerTitle')} data-testid="cancellation">
          {t('serviceOrder.cancel.bannerBody', {
            who: cancellation.cancelledBy ? (cancellation.cancelledByLabel ?? t('serviceOrder.history.unknownUser')) : t('serviceOrder.history.system'),
            when: formatDateTime(cancellation.cancelledAt, locale),
            reason: cancellation.reason,
          })}
        </Alert>
      )}

      <DetailTabs
        tabs={[
          { id: 'positions', label: t('serviceOrder.tabs.positions'), count: order.jobs.reduce((n, job) => n + job.positions.length, 0) },
          { id: 'overview', label: t('serviceOrder.tabs.overview') },
          { id: 'history', label: t('serviceOrder.tabs.history') },
        ]}
        activeTab={tab}
        onTabChange={(next) =>
          setSearchParams(
            (prev) => {
              const params = new URLSearchParams(prev)
              if (next === 'positions') params.delete('tab')
              else params.set('tab', next)
              return params
            },
            { replace: true },
          )
        }
      />

      {tab === 'positions' && <PositionsTab order={order} canWrite={canWrite} frozen={frozen} onOrder={setOrder} onReload={reload} />}
      {tab === 'overview' && <OverviewTab order={order} canWrite={canWrite} frozen={frozen} onOrder={setOrder} onReload={reload} />}
      {tab === 'history' && <HistoryTab orderId={order.id} version={order.version} />}

      <JobNameDialog
        opened={addJobOpen}
        title={t('serviceOrder.jobs.dialog.addTitle')}
        submitLabel={t('serviceOrder.jobs.dialog.add')}
        initialName=""
        onClose={() => setAddJobOpen(false)}
        onSave={async (name) => {
          const path = `/service-orders/${order.id}/jobs`
          // Slice 1 has one billing category, «Kunde» (the slice page, section 10).
          const body: ServiceOrderJobCreate = { name, billingCategory: 'customer' }
          const updated = await api.post<ServiceOrderRead>(path, body, {
            'If-Match': String(order.version),
            ...idempotency.headers([path, body]),
          })
          idempotency.renew()
          setOrder(updated)
          setAddJobOpen(false)
          if (tab !== 'positions') navigate({ search: '' }, { replace: true })
        }}
        onReload={reload}
      />
      <CancelServiceOrderDialog
        opened={cancelOpen}
        order={order}
        onClose={() => setCancelOpen(false)}
        onCancelled={(updated) => {
          setOrder(updated)
          setCancelOpen(false)
        }}
        onReload={reload}
      />
    </Stack>
  )
}
