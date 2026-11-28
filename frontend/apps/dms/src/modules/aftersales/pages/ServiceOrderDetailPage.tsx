import { useState } from 'react'
import { useNavigate, useParams, useSearchParams } from 'react-router-dom'
import { Alert, Loader, Stack } from '@mantine/core'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import { AlertTriangle, Ban, Car, UserCheck, Wrench } from 'lucide-react'
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
import { AnswerDialog } from '../components/order-detail/AnswerDialog'
import { ArrivalDialog } from '../components/order-detail/ArrivalDialog'
import { HistoryTab } from '../components/order-detail/HistoryTab'
import { JobNameDialog } from '../components/order-detail/OrderDialogs'
import { OverviewTab } from '../components/order-detail/OverviewTab'
import { PositionsTab } from '../components/order-detail/PositionsTab'
import { ReceptionTab } from '../components/order-detail/ReceptionTab'
import { OrderStateBadge } from '../components/OrderStateBadge'
import { WorkshopStatusBadge } from '../components/WorkshopStatusBadge'
import { buildServiceOrderRowMenu } from '../components/serviceOrderRowMenu'
import { OPEN_STATES } from '../serviceOrderOptions'

type Tab = 'positions' | 'overview' | 'reception' | 'history'
const TABS: Tab[] = ['positions', 'overview', 'reception', 'history']

/**
 * D3 — the workshop order (KAN-810 criterion 6): the kit's DetailHeader
 * («<vehicle> · <ordering party>», the number, the state, the workshop status
 * worked out by the server, KAN-813), one primary — «Antwort erfassen» (D8,
 * KAN-811) — one alternative, «Fahrzeug ist da» (D14, KAN-813), and the
 * overflow carrying the order's row menu (ADR-061; «Antwort erfassen»,
 * «Fahrzeug ist da», «Auftrag stornieren»). «Gruppe hinzufügen» sits on the
 * positions tab, as the design draws it; «Position» on each job.
 * `?dialog=answer` opens D8 and `?dialog=arrival` D14 (the list's row menu).
 * Tabs: Positionen, Übersicht, Annahme, Verlauf (the tab is in the URL).
 * «Arbeit ohne Antwort» is the server's, worked out (section 3.9). Every change is sent with the order's version (If-Match) and
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
  const dialog = searchParams.get('dialog')
  const setDialog = (name: 'answer' | 'arrival' | null) =>
    setSearchParams(
      (prev) => {
        const params = new URLSearchParams(prev)
        if (name) params.set('dialog', name)
        else params.delete('dialog')
        return params
      },
      { replace: true },
    )
  const answerOpen = dialog === 'answer'
  const setAnswerOpen = (open: boolean) => setDialog(open ? 'answer' : null)
  const arrivalOpen = dialog === 'arrival'
  const setArrivalOpen = (open: boolean) => setDialog(open ? 'arrival' : null)

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
  const menu = buildServiceOrderRowMenu(t, order, {
    onRecordAnswer: canWrite ? () => setAnswerOpen(true) : undefined,
    onArrive: canWrite ? () => setArrivalOpen(true) : undefined,
    onCancel: canWrite ? () => setCancelOpen(true) : undefined,
  })
  const frozenReason = frozen ? t('serviceOrder.actions.frozen') : undefined
  // Check 23: a car that is here is not received a second time — disabled with its reason.
  const carHere = Boolean(order.stay && !order.stay.departedAt)
  const arrivalReason = frozenReason ?? (carHere ? t('serviceOrder.arrival.alreadyHere') : undefined)
  const cancellation = order.cancellation

  return (
    <Stack gap="lg">
      <DetailHeader
        labels={{ moreActions: t('common.moreActions') }}
        entityMark={<Wrench size={24} />}
        title={`${order.vehicleLabel} · ${order.contact.label}`}
        businessKey={order.orderNumber}
        badges={
          <>
            <OrderStateBadge state={order.state} />
            <WorkshopStatusBadge status={order.workshopStatus ?? 'planned'} />
          </>
        }
        primaryAction={
          canWrite
            ? {
                label: t('serviceOrder.actions.recordAnswer'),
                icon: <UserCheck size={16} />,
                onClick: () => setAnswerOpen(true),
                disabled: frozen,
                disabledReason: frozenReason,
              }
            : undefined
        }
        alternativeAction={
          canWrite
            ? {
                label: t('serviceOrder.arrival.title'),
                icon: <Car size={16} />,
                onClick: () => setArrivalOpen(true),
                disabled: arrivalReason !== undefined,
                disabledReason: arrivalReason,
              }
            : undefined
        }
        overflowActions={menu.destructive || menu.edit ? menu : undefined}
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

      {order.workWithoutAnswer && (
        <Alert color="orange" icon={<AlertTriangle size={18} />} title={t('serviceOrder.answer.workWithoutAnswerTitle')} data-testid="work-without-answer">
          {t('serviceOrder.answer.workWithoutAnswerBody', {
            jobs: order.jobs.filter((job) => job.workWithoutAnswer).map((job) => job.name).join(', '),
          })}
        </Alert>
      )}

      <DetailTabs
        tabs={[
          { id: 'positions', label: t('serviceOrder.tabs.positions'), count: order.jobs.reduce((n, job) => n + job.positions.length, 0) },
          { id: 'overview', label: t('serviceOrder.tabs.overview') },
          { id: 'reception', label: t('serviceOrder.tabs.reception') },
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

      {tab === 'positions' && (
        <PositionsTab order={order} canWrite={canWrite} frozen={frozen} onOrder={setOrder} onReload={reload} onAddJob={() => setAddJobOpen(true)} />
      )}
      {tab === 'overview' && <OverviewTab order={order} canWrite={canWrite} frozen={frozen} onOrder={setOrder} onReload={reload} />}
      {tab === 'reception' && (
        <ReceptionTab order={order} onArrive={canWrite && arrivalReason === undefined ? () => setArrivalOpen(true) : undefined} />
      )}
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
      {canWrite && (
        <ArrivalDialog
          opened={arrivalOpen && arrivalReason === undefined}
          order={order}
          onClose={() => setArrivalOpen(false)}
          onSaved={(updated) => {
            setOrder(updated)
            setArrivalOpen(false)
          }}
          onReload={reload}
        />
      )}
      {canWrite && (
        <AnswerDialog
          opened={answerOpen && !frozen}
          order={order}
          onClose={() => setAnswerOpen(false)}
          onSaved={(updated) => {
            setOrder(updated)
            setAnswerOpen(false)
          }}
          onReload={reload}
        />
      )}
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
