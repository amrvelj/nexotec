import { useMemo } from 'react'
import { useParams, useSearchParams } from 'react-router-dom'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import { Building2, Copy, Loader, User } from 'lucide-react'
import { Alert, Menu } from '@mantine/core'
import {
  CustomerTypeBadge,
  DetailHeader,
  DetailTabs,
  LanguageBadge,
  LifecycleStatusBadge,
  useSetBreadcrumb,
  type DetailTab,
} from '@nexotec/ui-kit'
import { api, ApiError } from '../api/client'
import { OverviewTab } from '../components/customer-detail/OverviewTab'
import { VehiclesTab } from '../components/customer-detail/VehiclesTab'
import { TransactionsTab } from '../components/customer-detail/TransactionsTab'
import { HistoryTab } from '../components/customer-detail/HistoryTab'
import { ExternalIdsTab } from '../components/customer-detail/ExternalIdsTab'
import type {
  AuditEventPage,
  CustomerEmailPage,
  CustomerExternalIdPage,
  CustomerPhonePage,
  CustomerRead,
  CustomerUpdateInput,
  CustomerVehiclePage,
  TransactionPage,
} from '../api/types'

const DEFAULT_TAB = 'overview'

/**
 * FR-06 Customer 360 view. "The Customer 360 pattern generalises to every
 * entity" per the UI/UX doc's Detail Screens section — this page is the
 * app-specific wiring (data fetching, the customer field set, the actual
 * PATCH calls) around the generic DetailHeader/DetailTabs/InlineEditField
 * shell in @nexotec/ui-kit. Replaces the old CustomerFormPage entirely:
 * editing here is inline (FR-05), not a separate Save-button form.
 */
export function CustomerDetailPage() {
  const { id } = useParams<{ id: string }>()
  const [searchParams, setSearchParams] = useSearchParams()
  const queryClient = useQueryClient()
  const activeTab = searchParams.get('tab') ?? DEFAULT_TAB

  const setActiveTab = (tab: string) => {
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

  const customerQuery = useQuery({
    queryKey: ['customer', id],
    queryFn: () => api.get<CustomerRead>(`/customers/${id}`),
    enabled: Boolean(id),
  })
  const phonesQuery = useQuery({
    queryKey: ['customer', id, 'phones'],
    queryFn: () => api.get<CustomerPhonePage>(`/customers/${id}/phones`),
    enabled: Boolean(id),
  })
  const emailsQuery = useQuery({
    queryKey: ['customer', id, 'emails'],
    queryFn: () => api.get<CustomerEmailPage>(`/customers/${id}/emails`),
    enabled: Boolean(id),
  })
  const vehiclesQuery = useQuery({
    queryKey: ['customer', id, 'vehicles'],
    queryFn: () => api.get<CustomerVehiclePage>(`/customers/${id}/vehicles`),
    enabled: Boolean(id),
  })
  const transactionsQuery = useQuery({
    queryKey: ['customer', id, 'transactions'],
    queryFn: () => api.get<TransactionPage>(`/transactions?customer_id=${id}`),
    enabled: Boolean(id),
  })
  const externalIdsQuery = useQuery({
    queryKey: ['customer', id, 'external-ids'],
    queryFn: () => api.get<CustomerExternalIdPage>(`/customers/${id}/external-ids`),
    enabled: Boolean(id),
  })
  const historyQuery = useQuery({
    queryKey: ['customer', id, 'history'],
    queryFn: () => api.get<AuditEventPage>(`/customers/${id}/audit-log`),
    enabled: Boolean(id),
  })

  const customer = customerQuery.data
  const label = customer ? (customer.customerType === 'business' ? customer.companyName : `${customer.firstName} ${customer.lastName}`) : null
  useSetBreadcrumb(['Master Data', 'Customers', label ?? 'Customer'])

  const isConflict = (err: unknown): boolean => err instanceof ApiError && err.status === 409

  const saveField = async (patch: Partial<CustomerUpdateInput>) => {
    const current = customerQuery.data
    if (!current || !id) throw new Error('Customer not loaded.')
    const updated = await api.patch<CustomerRead>(`/customers/${id}`, patch, { 'If-Match': String(current.version) })
    queryClient.setQueryData(['customer', id], updated)
    // Every PATCH writes a new audit_event row server-side — the History
    // tab's cached page is now stale even though it isn't what we just
    // wrote to, so it needs its own invalidation rather than relying on
    // the customer cache update above.
    void queryClient.invalidateQueries({ queryKey: ['customer', id, 'history'] })
  }

  const reload = () => {
    void customerQuery.refetch()
  }

  const tabs: DetailTab[] = useMemo(
    () => [
      { id: 'overview', label: 'Overview' },
      { id: 'vehicles', label: 'Vehicles', count: vehiclesQuery.data?.items.length },
      { id: 'transactions', label: 'Transactions', count: transactionsQuery.data?.items.length },
      { id: 'history', label: 'History', count: historyQuery.data?.items.length },
      { id: 'external-ids', label: 'External IDs', count: externalIdsQuery.data?.items.length },
    ],
    [vehiclesQuery.data, transactionsQuery.data, historyQuery.data, externalIdsQuery.data]
  )

  if (customerQuery.isLoading) return <Loader />
  if (customerQuery.isError || !customer) {
    return (
      <Alert color="red" title="Failed to load customer">
        {customerQuery.error instanceof ApiError ? customerQuery.error.message : 'Something went wrong.'}
      </Alert>
    )
  }

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 20 }}>
      <DetailHeader
        entityMark={customer.customerType === 'business' ? <Building2 size={22} /> : <User size={22} />}
        title={label ?? customer.customerNumber}
        businessKey={customer.customerNumber}
        badges={
          <>
            <CustomerTypeBadge type={customer.customerType} />
            <LifecycleStatusBadge status={customer.lifecycleStatus} />
            <LanguageBadge language={customer.language} />
          </>
        }
        overflowItems={
          <Menu.Item leftSection={<Copy size={16} />} onClick={() => navigator.clipboard.writeText(customer.customerNumber)}>
            Copy customer number
          </Menu.Item>
        }
      />

      <DetailTabs tabs={tabs} activeTab={activeTab} onTabChange={setActiveTab} />

      {activeTab === 'overview' && (
        <OverviewTab
          customer={customer}
          phones={phonesQuery.data?.items ?? []}
          emails={emailsQuery.data?.items ?? []}
          onSaveField={saveField}
          isConflict={isConflict}
          onReload={reload}
        />
      )}
      {activeTab === 'vehicles' && (
        <VehiclesTab
          vehicles={vehiclesQuery.data?.items ?? []}
          loading={vehiclesQuery.isLoading}
          error={vehiclesQuery.isError ? 'Failed to load vehicles.' : null}
        />
      )}
      {activeTab === 'transactions' && (
        <TransactionsTab
          transactions={transactionsQuery.data?.items ?? []}
          loading={transactionsQuery.isLoading}
          error={transactionsQuery.isError ? 'Failed to load transactions.' : null}
        />
      )}
      {activeTab === 'history' && (
        <HistoryTab
          events={historyQuery.data?.items ?? []}
          loading={historyQuery.isLoading}
          error={historyQuery.isError ? (historyQuery.error instanceof ApiError ? historyQuery.error.message : 'Failed to load history.') : null}
        />
      )}
      {activeTab === 'external-ids' && (
        <ExternalIdsTab
          externalIds={externalIdsQuery.data?.items ?? []}
          loading={externalIdsQuery.isLoading}
          error={externalIdsQuery.isError ? 'Failed to load external IDs.' : null}
        />
      )}
    </div>
  )
}
