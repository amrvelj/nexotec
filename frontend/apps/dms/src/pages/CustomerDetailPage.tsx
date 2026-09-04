import { useMemo, useState } from 'react'
import { useNavigate, useParams, useSearchParams } from 'react-router-dom'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import { Building2, Car, Copy, GitMerge, Handshake, Loader, PhoneOff, Pencil, User } from 'lucide-react'
import { Alert } from '@mantine/core'
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
import { OverviewTab, type AddressDraft } from '../components/customer-detail/OverviewTab'
import type { ContactPointUpdatePatch } from '../components/customer-detail/ContactPointsEditor'
import { VehiclesTab } from '../components/customer-detail/VehiclesTab'
import { TransactionsTab } from '../components/customer-detail/TransactionsTab'
import { HistoryTab } from '../components/customer-detail/HistoryTab'
import { ExternalIdsTab } from '../components/customer-detail/ExternalIdsTab'
import { MergeCustomerModal } from '../components/customer-detail/MergeCustomerModal'
import { LinkVehicleModal } from '../components/customer-detail/LinkVehicleModal'
import type {
  AuditEventPage,
  CustomerAddressRead,
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
  SalesOfferRead,
  TransactionPage,
} from '../api/types'

const DEFAULT_TAB = 'overview'

/** The real route — reads `id` from the URL and renders as the full page. */
export function CustomerDetailPage() {
  const { id } = useParams<{ id: string }>()
  if (!id) return null
  return <CustomerDetailContent customerId={id} />
}

export interface CustomerDetailContentProps {
  customerId: string
  /**
   * § ADR-059 — true when rendered as `Overlay` content rather than the
   * real `/customers/:id` route. Two things change: tab state moves from
   * the URL's own `?tab=` to local component state (Overlay's content
   * shares the HOST screen's actual address bar — a vehicle detail screen
   * opened underneath already owns `?tab=` for its own tabs, and writing
   * to it here would fight the screen this is layered on top of, exactly
   * the "renders on top... without touching the URL" ADR-059 itself
   * exists to guarantee); and the breadcrumb is left untouched entirely
   * (`useSetBreadcrumb(null)`) rather than overwriting the host screen's.
   */
  embedded?: boolean
}

/**
 * FR-06 Customer 360 view. "The Customer 360 pattern generalises to every
 * entity" per the UI/UX doc's Detail Screens section — this page is the
 * app-specific wiring (data fetching, the customer field set, the actual
 * PATCH calls) around the generic DetailHeader/DetailTabs/InlineEditField
 * shell in @nexotec/ui-kit. Replaces the old CustomerFormPage entirely:
 * editing here is inline (FR-05), not a separate Save-button form.
 *
 * Exported separately from the route (`CustomerDetailPage` above) so this
 * same content can also render as `Overlay` content, prop-driven by
 * `customerId` instead of `useParams()` — the "second, prop-driven entry
 * point" `Overlay.tsx`'s own docstring says a screen normally reached via
 * `useParams()` needs to be usable inside one.
 */
export function CustomerDetailContent({ customerId: id, embedded = false }: CustomerDetailContentProps) {
  const navigate = useNavigate()
  const { t, i18n } = useTranslation()
  const locale = toSwissLocale(i18n.language as SupportedLanguage)
  const { user } = useAuth()
  const canWriteExternalIds = user?.accessRoles.includes('platform_admin') ?? false
  const [searchParams, setSearchParams] = useSearchParams()
  const [embeddedTab, setEmbeddedTab] = useState(DEFAULT_TAB)
  const queryClient = useQueryClient()
  const activeTab = embedded ? embeddedTab : (searchParams.get('tab') ?? DEFAULT_TAB)
  const [mergeModalOpen, setMergeModalOpen] = useState(false)
  const [linkVehicleOpen, setLinkVehicleOpen] = useState(false)
  const [creatingOffer, setCreatingOffer] = useState(false)

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
  useSetBreadcrumb(embedded ? null : [t('shell.nav.masterData'), t('shell.nav.customers'), label ?? t('customerDetail.header.customerFallback')])

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

  // KAN-30: the address lives on /customers/{id}/addresses (ADR-067), a
  // child row like phone/email, never a PATCH-able field on the customer
  // itself — CustomerUpdate genuinely has no `address`. Unlike phone/email
  // there is only ever one slot on this screen (the primary domicile
  // address, `customer.address`), so this owns the POST-if-absent /
  // PATCH-if-present / DELETE-if-cleared decision that a repeatable row
  // group would otherwise make per-row.
  const saveAddress = async (draft: AddressDraft) => {
    const existing: CustomerAddressRead | null = customerQuery.data?.address ?? null
    const hasAny = Boolean(draft.street || draft.houseNumber || draft.postalCode || draft.locality)
    if (!hasAny) {
      if (existing) await api.delete(`/customers/${id}/addresses/${existing.id}`)
    } else {
      const body = {
        addressType: 'domicile' as const,
        addressStreet: draft.street,
        addressHouseNumber: draft.houseNumber,
        addressPostalCode: draft.postalCode,
        addressLocality: draft.locality,
        addressCountry: existing?.addressCountry ?? 'CH',
        isPrimary: true,
      }
      if (existing) {
        await api.patch<CustomerAddressRead>(`/customers/${id}/addresses/${existing.id}`, body)
      } else {
        await api.post<CustomerAddressRead>(`/customers/${id}/addresses`, body)
      }
    }
    // The address is embedded on the customer resource itself
    // (CustomerRead.address), unlike phones/emails' own list endpoint —
    // refetching the customer is what picks up the change.
    void queryClient.invalidateQueries({ queryKey: ['customer', id] })
    void queryClient.invalidateQueries({ queryKey: ['customer', id, 'history'] })
  }

  // KAN-14 / FR-22 — the alternative action. `POST /sales/offers` takes
  // no body (a bare offer only becomes "for someone" via the same PATCH
  // the workspace's own customer-picker already uses) — this is that
  // same two-call sequence, just started from the customer's own side.
  const createOfferForCustomer = async () => {
    if (!id) return
    setCreatingOffer(true)
    try {
      const created = await api.post<SalesOfferRead>('/sales/offers')
      const updated = await api.patch<SalesOfferRead>(
        `/sales/offers/${created.id}`,
        { customerId: id },
        { 'If-Match': String(created.version) }
      )
      navigate(`/sales/offers/${updated.id}`)
    } finally {
      setCreatingOffer(false)
    }
  }

  // KAN-14 / FR-22 / FR-12 — toggles the existing `lifecycleStatus` field
  // (already a plain PATCH-able field, `saveField` above) rather than a
  // new endpoint. Reverts to `active` when unset — ADR-065's own
  // distinction is that do-not-contact stops contact, never credit, and
  // is always reversible, unlike merge/anonymise.
  const toggleDoNotContact = () => {
    void saveField({ lifecycleStatus: customer?.lifecycleStatus === 'do_not_contact' ? 'active' : 'do_not_contact' })
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
  const updatePhone = async (phoneId: string, patch: ContactPointUpdatePatch<PhoneType>) => {
    await api.patch<CustomerPhoneRead>(`/customers/${id}/phones/${phoneId}`, {
      phoneType: patch.type,
      label: patch.label,
      phoneE164: patch.value,
      isPrimary: patch.isPrimary,
      validTo: patch.validTo,
      doNotUse: patch.doNotUse,
      doNotUseReason: patch.doNotUseReason,
      consentGranted: patch.consentGranted,
      consentSource: patch.consentSource,
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
  const updateEmail = async (emailId: string, patch: ContactPointUpdatePatch<EmailType>) => {
    await api.patch<CustomerEmailRead>(`/customers/${id}/emails/${emailId}`, {
      emailType: patch.type,
      label: patch.label,
      emailAddress: patch.value,
      isPrimary: patch.isPrimary,
      validTo: patch.validTo,
      doNotUse: patch.doNotUse,
      doNotUseReason: patch.doNotUseReason,
      consentGranted: patch.consentGranted,
      consentSource: patch.consentSource,
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
        entityMark={customer.customerType === 'business' ? <Building2 size={24} /> : <User size={24} />}
        title={label ?? customer.customerNumber}
        businessKey={customer.customerNumber}
        badges={
          <>
            <CustomerTypeBadge type={customer.customerType} label={translatedCustomerTypeLabel(t, customer.customerType)} />
            <LifecycleStatusBadge status={customer.lifecycleStatus} label={translatedLifecycleLabel(t, customer.lifecycleStatus)} />
            <LanguageBadge language={customer.language} />
          </>
        }
        // KAN-14 / FR-22 (ADR-061) — primary "Edit" and alternative "New
        // offer" were both entirely absent before this fix; only a
        // 2-item overflow existed. "Edit" here switches to the Overview
        // tab, where every field is already inline-editable (the PRD's
        // own 2026-08-21 amendment: "editing one value on a record
        // already on screen is inline") — there is no separate edit-mode
        // concept anywhere else in this codebase to mirror instead. This
        // is a judgment call, not a confirmed product decision; worth a
        // second look if "Edit" is meant to do something more specific.
        primaryAction={{
          label: t('customerDetail.header.edit'),
          icon: <Pencil size={16} />,
          onClick: () => setActiveTab('overview'),
        }}
        alternativeAction={{
          label: t('customerDetail.header.newOffer'),
          icon: <Handshake size={16} />,
          onClick: () => void createOfferForCustomer(),
          disabled: creatingOffer || customer.lifecycleStatus === 'do_not_contact',
          disabledReason: customer.lifecycleStatus === 'do_not_contact' ? t('customerDetail.header.newOfferDisabledReason') : undefined,
        }}
        overflowActions={{
          exportPrint: [
            {
              label: t('customerDetail.header.copyCustomerNumber'),
              icon: <Copy size={16} />,
              onClick: () => navigator.clipboard.writeText(customer.customerNumber),
            },
          ],
          edit: [
            {
              label: t('customerDetail.header.linkVehicle'),
              icon: <Car size={16} />,
              onClick: () => setLinkVehicleOpen(true),
            },
            {
              label:
                customer.lifecycleStatus === 'do_not_contact'
                  ? t('customerDetail.header.removeDoNotContact')
                  : t('customerDetail.header.setDoNotContact'),
              icon: <PhoneOff size={16} />,
              onClick: toggleDoNotContact,
            },
            // FR-14 (anonymisation) is explicitly "Not implemented —
            // required before production, not before the Phase B UI" in
            // PRD-Customers' own text — deliberately not built here.
          ],
          destructive:
            customer.lifecycleStatus !== 'merged'
              ? [
                  {
                    label: t('customerDetail.header.mergeInto'),
                    icon: <GitMerge size={16} />,
                    onClick: () => setMergeModalOpen(true),
                  },
                ]
              : [],
        }}
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
          onSaveAddress={saveAddress}
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

      {id && (
        <LinkVehicleModal
          opened={linkVehicleOpen}
          onClose={() => setLinkVehicleOpen(false)}
          customerId={id}
          onLinked={() => void queryClient.invalidateQueries({ queryKey: ['customer', id, 'vehicles'] })}
        />
      )}
    </div>
  )
}
