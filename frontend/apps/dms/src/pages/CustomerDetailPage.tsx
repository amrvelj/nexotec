import { useMemo, useState } from 'react'
import { useNavigate, useParams, useSearchParams } from 'react-router-dom'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import { Building2, Copy, GitMerge, Loader, User } from 'lucide-react'
import { Alert, Menu } from '@mantine/core'
import { useTranslation } from 'react-i18next'
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
import { useAuth } from '../auth/AuthContext'
import { toSwissLocale, type SupportedLanguage } from '../i18n'
import { translatedCustomerTypeLabel, translatedLifecycleLabel } from '../customerOptions'
import { OverviewTab } from '../components/customer-detail/OverviewTab'
import { VehiclesTab } from '../components/customer-detail/VehiclesTab'
import { TransactionsTab } from '../components/customer-detail/TransactionsTab'
import { HistoryTab } from '../components/customer-detail/HistoryTab'
import { ExternalIdsTab } from '../components/customer-detail/ExternalIdsTab'
import { MergeCustomerModal } from '../components/customer-detail/MergeCustomerModal'
import type {
  AuditEventPage,
  CustomerEmailPage,
  CustomerEmailRead,
  CustomerExternalIdPage,
  CustomerExternalIdRead,
  CustomerPhonePage,
  CustomerPhoneRead,
  CustomerRead,
  CustomerUpdateInput,
  CustomerVehiclePage,
  EmailType,
  PhoneType,
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
  const navigate = useNavigate()
  const { t, i18n } = useTranslation()
  const locale = toSwissLocale(i18n.language as SupportedLanguage)
  const { user } = useAuth()
  const canWriteExternalIds = user?.accessRoles.includes('platform_admin') ?? false
  const [searchParams, setSearchParams] = useSearchParams()
  const queryClient = useQueryClient()
  const activeTab = searchParams.get('tab') ?? DEFAULT_TAB
  const [mergeModalOpen, setMergeModalOpen] = useState(false)

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
  useSetBreadcrumb([t('shell.nav.masterData'), t('shell.nav.customers'), label ?? t('customerDetail.header.customerFallback')])

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

  // FR-07: phone/email rows have no version column ("is_primary isn't a
  // high-contention field") — no If-Match here, unlike saveField above.
  // Every mutation still writes an audit event server-side (phone_add,
  // phone_remove, ...), so history needs the same invalidation.
  const invalidateContact = (kind: 'phones' | 'emails') => {
    void queryClient.invalidateQueries({ queryKey: ['customer', id, kind] })
    void queryClient.invalidateQueries({ queryKey: ['customer', id, 'history'] })
  }

  const createPhone = async (row: { type: PhoneType; value: string }) => {
    await api.post<CustomerPhoneRead>(`/customers/${id}/phones`, { phoneType: row.type, phoneE164: row.value })
    invalidateContact('phones')
  }
  const updatePhone = async (phoneId: string, patch: { type?: PhoneType; value?: string; isPrimary?: boolean }) => {
    await api.patch<CustomerPhoneRead>(`/customers/${id}/phones/${phoneId}`, {
      phoneType: patch.type,
      phoneE164: patch.value,
      isPrimary: patch.isPrimary,
    })
    invalidateContact('phones')
  }
  const deletePhone = async (phoneId: string) => {
    await api.delete(`/customers/${id}/phones/${phoneId}`)
    invalidateContact('phones')
  }

  const createEmail = async (row: { type: EmailType; value: string }) => {
    await api.post<CustomerEmailRead>(`/customers/${id}/emails`, { emailType: row.type, emailAddress: row.value })
    invalidateContact('emails')
  }
  const updateEmail = async (emailId: string, patch: { type?: EmailType; value?: string; isPrimary?: boolean }) => {
    await api.patch<CustomerEmailRead>(`/customers/${id}/emails/${emailId}`, {
      emailType: patch.type,
      emailAddress: patch.value,
      isPrimary: patch.isPrimary,
    })
    invalidateContact('emails')
  }
  const deleteEmail = async (emailId: string) => {
    await api.delete(`/customers/${id}/emails/${emailId}`)
    invalidateContact('emails')
  }

  // D-08: write is platform_admin-only (see app/models/customer.py's
  // CustomerExternalId docstring) — no version column either, same
  // no-If-Match shape as the contact-point handlers above.
  const invalidateExternalIds = () => {
    void queryClient.invalidateQueries({ queryKey: ['customer', id, 'external-ids'] })
    void queryClient.invalidateQueries({ queryKey: ['customer', id, 'history'] })
  }

  const createExternalId = async (row: { systemName: string; externalId: string }) => {
    await api.post<CustomerExternalIdRead>(`/customers/${id}/external-ids`, { systemName: row.systemName, externalId: row.externalId })
    invalidateExternalIds()
  }
  const updateExternalId = async (rowId: string, patch: { systemName?: string; externalId?: string }) => {
    await api.patch<CustomerExternalIdRead>(`/customers/${id}/external-ids/${rowId}`, {
      systemName: patch.systemName,
      externalId: patch.externalId,
    })
    invalidateExternalIds()
  }
  const deleteExternalId = async (rowId: string) => {
    await api.delete(`/customers/${id}/external-ids/${rowId}`)
    invalidateExternalIds()
  }

  const tabs: DetailTab[] = useMemo(
    () => [
      { id: 'overview', label: t('customerDetail.tabs.overview') },
      { id: 'vehicles', label: t('customerDetail.tabs.vehicles'), count: vehiclesQuery.data?.items.length },
      { id: 'transactions', label: t('customerDetail.tabs.transactions'), count: transactionsQuery.data?.items.length },
      { id: 'history', label: t('customerDetail.tabs.history'), count: historyQuery.data?.items.length },
      { id: 'external-ids', label: t('customerDetail.tabs.externalIds'), count: externalIdsQuery.data?.items.length },
    ],
    [t, vehiclesQuery.data, transactionsQuery.data, historyQuery.data, externalIdsQuery.data]
  )

  if (customerQuery.isLoading) return <Loader />
  if (customerQuery.isError || !customer) {
    return (
      <Alert color="red" title={t('customerDetail.header.failedToLoad')}>
        {customerQuery.error instanceof ApiError ? customerQuery.error.message : t('customerDetail.errors.somethingWentWrong')}
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
            <CustomerTypeBadge type={customer.customerType} label={translatedCustomerTypeLabel(t, customer.customerType)} />
            <LifecycleStatusBadge status={customer.lifecycleStatus} label={translatedLifecycleLabel(t, customer.lifecycleStatus)} />
            <LanguageBadge language={customer.language} />
          </>
        }
        overflowItems={
          <>
            <Menu.Item leftSection={<Copy size={16} />} onClick={() => navigator.clipboard.writeText(customer.customerNumber)}>
              {t('customerDetail.header.copyCustomerNumber')}
            </Menu.Item>
            {customer.lifecycleStatus !== 'merged' && (
              <Menu.Item leftSection={<GitMerge size={16} />} color="red" onClick={() => setMergeModalOpen(true)}>
                {t('customerDetail.header.mergeInto')}
              </Menu.Item>
            )}
          </>
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
          onCreatePhone={createPhone}
          onUpdatePhone={updatePhone}
          onDeletePhone={deletePhone}
          onCreateEmail={createEmail}
          onUpdateEmail={updateEmail}
          onDeleteEmail={deleteEmail}
          locale={locale}
        />
      )}
      {activeTab === 'vehicles' && (
        <VehiclesTab
          vehicles={vehiclesQuery.data?.items ?? []}
          loading={vehiclesQuery.isLoading}
          error={vehiclesQuery.isError ? t('customerDetail.errors.failedToLoadVehicles') : null}
          locale={locale}
        />
      )}
      {activeTab === 'transactions' && (
        <TransactionsTab
          transactions={transactionsQuery.data?.items ?? []}
          loading={transactionsQuery.isLoading}
          error={transactionsQuery.isError ? t('customerDetail.errors.failedToLoadTransactions') : null}
          locale={locale}
        />
      )}
      {activeTab === 'history' && (
        <HistoryTab
          events={historyQuery.data?.items ?? []}
          loading={historyQuery.isLoading}
          error={
            historyQuery.isError
              ? historyQuery.error instanceof ApiError
                ? historyQuery.error.message
                : t('customerDetail.errors.failedToLoadHistory')
              : null
          }
          locale={locale}
        />
      )}
      {activeTab === 'external-ids' && (
        <ExternalIdsTab
          externalIds={externalIdsQuery.data?.items ?? []}
          loading={externalIdsQuery.isLoading}
          error={externalIdsQuery.isError ? t('customerDetail.errors.failedToLoadExternalIds') : null}
          canWrite={canWriteExternalIds}
          onCreate={createExternalId}
          onUpdate={updateExternalId}
          onDelete={deleteExternalId}
          locale={locale}
        />
      )}

      <MergeCustomerModal
        opened={mergeModalOpen}
        onClose={() => setMergeModalOpen(false)}
        customer={customer}
        phones={phonesQuery.data?.items ?? []}
        emails={emailsQuery.data?.items ?? []}
        onMerged={(survivorId) => {
          setMergeModalOpen(false)
          void queryClient.invalidateQueries({ queryKey: ['customers'] })
          navigate(`/customers/${survivorId}`)
        }}
      />
    </div>
  )
}
