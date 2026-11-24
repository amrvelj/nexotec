// The Contact 360 content (ADR-059's prop-driven entry point), out of
// pages/ContactDetailPage.tsx so other screens render it without importing a
// page (ADR-161 amendment, KAN-636 step 2a: a module's door never names a page).
import { useMemo, useState } from 'react'
import { useNavigate, useSearchParams } from 'react-router-dom'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import { Building2, Loader, User } from 'lucide-react'
import { Alert } from '@mantine/core'
import { useTranslation } from 'react-i18next'
import {
  Badge,
  ContactTypeBadge,
  DetailHeader,
  DetailTabs,
  LanguageBadge,
  LifecycleStatusBadge,
  useSetBreadcrumb,
  type DetailTab,
} from '@nexotec/ui-kit'
import { api } from '../../../../api/client'
import { useAuth } from '../../../../auth/AuthContext'
import { useCanWrite } from '../../../../auth/useCanWrite'
import { holdsNowOrLater } from '../../../../shared/partyPeriod'
import { toSwissLocale, type SupportedLanguage } from '../../../../i18n'
import { showsLifecycleStatus, translatedContactTypeLabel, translatedLifecycleLabel, translatedRoleLabel } from '../../contactOptions'
import { buildContactRowMenu } from '../contactRowMenu'
import { OverviewTab, type AddressDraft } from './OverviewTab'
import { useContactGroups } from '../../../../hooks/useContactGroups'
import { useCountryOptions } from '../../../../hooks/useCountryOptions'
import { useIdempotencyKey } from '../../../../hooks/useIdempotencyKey'
import type { ContactPointUpdatePatch } from './ContactPointsEditor'
import { VehiclesTab } from './VehiclesTab'
import { vehicleLabel } from './vehicleLabel'
import { OffersContractsTab, toContactDealRows } from './OffersContractsTab'
import { HistoryTab } from './HistoryTab'
import { ExternalIdsTab } from './ExternalIdsTab'
import { MergeContactModal } from './MergeContactModal'
import { CreditBlockDialog } from './CreditBlockDialog'
import { LinkVehicleModal } from './LinkVehicleModal'
import type {
  ContactHistoryPage,
  ContactAddressRead,
  ContactEmailPage,
  ContactEmailRead,
  ContactExternalIdPage,
  ContactExternalIdRead,
  ContactPhonePage,
  ContactPhoneRead,
  ContactRead,
  ContactUpdateInput,
  ContactVehiclePage,
  ContactVehicleRead,
  EmailType,
  PhoneType,
  SalesContractPage,
  SalesContractRead,
  SalesOfferPage,
  SalesOfferRead,
} from '../../../../api/types'
import { apiRefusalText, isStaleVersion } from '../../../../utils/apiRefusal'

const DEFAULT_TAB = 'overview'

export interface ContactDetailContentProps {
  contactId: string
  /**
   * § ADR-059 — true when rendered as `Overlay` content rather than the
   * real `/contacts/:id` route. Two things change: tab state moves from
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
 * FR-06 Contact 360 view. "The Contact 360 pattern generalises to every
 * entity" per the UI/UX doc's Detail Screens section — this page is the
 * app-specific wiring (data fetching, the customer field set, the actual
 * PATCH calls) around the generic DetailHeader/DetailTabs/InlineEditField
 * shell in @nexotec/ui-kit. Replaces the old ContactFormPage entirely:
 * editing here is inline (FR-05), not a separate Save-button form.
 *
 * Kept apart from the route (`ContactDetailPage`, in pages/) so this
 * same content can also render as `Overlay` content, prop-driven by
 * `contactId` instead of `useParams()` — the "second, prop-driven entry
 * point" `Overlay.tsx`'s own docstring says a screen normally reached via
 * `useParams()` needs to be usable inside one.
 */
export function ContactDetailContent({ contactId: id, embedded = false }: ContactDetailContentProps) {
  const navigate = useNavigate()
  const { t, i18n } = useTranslation()
  const contactGroups = useContactGroups()
  const locale = toSwissLocale(i18n.language as SupportedLanguage)
  const { options: countryOptions } = useCountryOptions()
  const { user, entitlements } = useAuth()
  // KAN-377 (D-A-04): a Betrieb without the sales module gets 404 from every
  // `/sales/*` route, so its contact screen neither reads offers/contracts
  // nor offers to create one. Presentation only — the 404 is the boundary.
  const salesEntitled = entitlements.includes('sales')
  const canWriteExternalIds = user?.accessRoles.includes('platform_admin') ?? false
  const [searchParams, setSearchParams] = useSearchParams()
  const [embeddedTab, setEmbeddedTab] = useState(DEFAULT_TAB)
  const queryClient = useQueryClient()
  const requestedTab = embedded ? embeddedTab : (searchParams.get('tab') ?? DEFAULT_TAB)
  // KAN-377: a pasted ?tab=offersContracts on a Betrieb without sales opens
  // the default tab, not an empty pane.
  const activeTab = requestedTab === 'offersContracts' && !salesEntitled ? DEFAULT_TAB : requestedTab
  const [mergeModalOpen, setMergeModalOpen] = useState(false)
  const [linkVehicleOpen, setLinkVehicleOpen] = useState(false)
  const [creditBlockOpen, setCreditBlockOpen] = useState(false)
  const [creatingOffer, setCreatingOffer] = useState(false)
  const [creatingContract, setCreatingContract] = useState(false)
  const [createError, setCreateError] = useState<string | null>(null)
  // KAN-266: one Idempotency-Key per submission of each create — a retry
  // after a failed save reuses it, so the server replays instead of
  // creating the row twice; a different request (another value, another
  // contact: this screen stays mounted when :id changes) gets a new one.
  // Every successful write of a kind (create, update, delete) renews that
  // kind's key: a key left by a create whose response was lost would
  // otherwise replay, creating nothing, when the same value is added again
  // after that row was edited or deleted.
  const addressKey = useIdempotencyKey()
  const phoneKey = useIdempotencyKey()
  const emailKey = useIdempotencyKey()
  const externalIdKey = useIdempotencyKey()
  // The "new offer" and "new contract" actions pass this contact as the
  // request, so a retry keeps its key and replays the first create.
  const offerCreateKey = useIdempotencyKey()
  const contractCreateKey = useIdempotencyKey()

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

  const contactQuery = useQuery({
    queryKey: ['customer', id],
    queryFn: () => api.get<ContactRead>(`/contacts/${id}`),
    enabled: Boolean(id),
  })
  const phonesQuery = useQuery({
    queryKey: ['customer', id, 'phones'],
    queryFn: () => api.get<ContactPhonePage>(`/contacts/${id}/phones`),
    enabled: Boolean(id),
  })
  const emailsQuery = useQuery({
    queryKey: ['customer', id, 'emails'],
    queryFn: () => api.get<ContactEmailPage>(`/contacts/${id}/emails`),
    enabled: Boolean(id),
  })
  // KAN-583 (ruling 2A): «Eintrag beenden» on the Vehicles tab — Disconnect,
  // which ends a current entry now and cancels a planned one.
  const canEndVehicleEntry = useCanWrite('contact_vehicle_links')
  const mayWriteCustomerRole = useCanWrite('contact_customer_role')
  const endVehicleEntry = async (row: ContactVehicleRead) => {
    await api.delete(`/contacts/${id}/vehicles/${row.id}`)
    void queryClient.invalidateQueries({ queryKey: ['customer', id, 'vehicles'] })
  }
  const vehiclesQuery = useQuery({
    queryKey: ['customer', id, 'vehicles'],
    // KAN-49 / FR-19 — include_closed=true: the tab shows this contact's
    // historical (ended) roles too, marked as ended via effectiveTo.
    queryFn: () => api.get<ContactVehiclePage>(`/contacts/${id}/vehicles?include_closed=true`),
    enabled: Boolean(id),
  })
  // FR-06 tab 3 / ADR-050 — the retired `transaction` table is gone; a
  // contact's deals are their offers and contracts, filtered by contactId
  // (KAN-45). Dealership-scoped, not group-scoped — see OffersContractsTab.
  const offersQuery = useQuery({
    queryKey: ['customer', id, 'offers'],
    queryFn: () => api.get<SalesOfferPage>(`/sales/offers?contact_id=${id}&limit=100`),
    enabled: Boolean(id) && salesEntitled,
  })
  const contractsQuery = useQuery({
    queryKey: ['customer', id, 'contracts'],
    queryFn: () => api.get<SalesContractPage>(`/sales/contracts?contact_id=${id}&limit=100`),
    enabled: Boolean(id) && salesEntitled,
  })
  const externalIdsQuery = useQuery({
    queryKey: ['customer', id, 'external-ids'],
    queryFn: () => api.get<ContactExternalIdPage>(`/contacts/${id}/external-ids`),
    enabled: Boolean(id),
  })
  const historyQuery = useQuery({
    queryKey: ['customer', id, 'history'],
    queryFn: () => api.get<ContactHistoryPage>(`/contacts/${id}/audit-log`),
    enabled: Boolean(id),
  })

  const contact = contactQuery.data
  const label = contact ? (contact.contactType === 'business' ? contact.companyName : `${contact.firstName} ${contact.lastName}`) : null
  useSetBreadcrumb(embedded ? null : [t('shell.nav.masterData'), { label: t('shell.nav.contacts'), href: '/contacts' }, label ?? t('contactDetail.header.contactFallback')])

  // KAN-473: only a stale If-Match 409 offers the reload.
  const isConflict = isStaleVersion

  const saveField = async (patch: Partial<ContactUpdateInput>) => {
    const current = contactQuery.data
    if (!current || !id) throw new Error('Contact not loaded.')
    const updated = await api.patch<ContactRead>(`/contacts/${id}`, patch, { 'If-Match': String(current.version) })
    queryClient.setQueryData(['customer', id], updated)
    // Every PATCH writes a new audit_event row server-side — the History
    // tab's cached page is now stale even though it isn't what we just
    // wrote to, so it needs its own invalidation rather than relying on
    // the contact cache update above.
    void queryClient.invalidateQueries({ queryKey: ['customer', id, 'history'] })
  }

  // KAN-30: the address lives on /contacts/{id}/addresses (ADR-067), a
  // child row like phone/email, never a PATCH-able field on the contact
  // itself — ContactUpdate genuinely has no `address`. Unlike phone/email
  // there is only ever one slot on this screen (the primary domicile
  // address, `contact.address`), so this owns the POST-if-absent /
  // PATCH-if-present / DELETE-if-cleared decision that a repeatable row
  // group would otherwise make per-row.
  const saveAddress = async (draft: AddressDraft) => {
    const existing: ContactAddressRead | null = contactQuery.data?.address ?? null
    const hasAny = Boolean(draft.street || draft.houseNumber || draft.postalCode || draft.locality)
    if (!hasAny) {
      if (existing) await api.delete(`/contacts/${id}/addresses/${existing.id}`)
    } else {
      const body = {
        addressType: 'domicile' as const,
        addressStreet: draft.street,
        addressLine2: draft.line2 || null,
        addressHouseNumber: draft.houseNumber,
        addressPostalCode: draft.postalCode,
        addressLocality: draft.locality,
        addressCountry: draft.country || existing?.addressCountry || 'CH',
        isPrimary: true,
      }
      if (existing) {
        await api.patch<ContactAddressRead>(`/contacts/${id}/addresses/${existing.id}`, body)
      } else {
        const path = `/contacts/${id}/addresses`
        try {
          await api.post<ContactAddressRead>(path, body, addressKey.headers([path, body]))
        } catch (err) {
          // A response lost on the way back may still have created the row.
          // Refetch before the error shows, so the next save PATCHes that row
          // instead of adding a second address this screen would never show.
          await queryClient.invalidateQueries({ queryKey: ['customer', id] })
          throw err
        }
      }
    }
    addressKey.renew()
    // The address is embedded on the contact resource itself
    // (ContactRead.address), unlike phones/emails' own list endpoint —
    // refetching the contact is what picks up the change.
    void queryClient.invalidateQueries({ queryKey: ['customer', id] })
    void queryClient.invalidateQueries({ queryKey: ['customer', id, 'history'] })
  }

  // KAN-14 / FR-22 — the alternative action. `POST /sales/offers` takes
  // no body (a bare offer only becomes "for someone" via the same PATCH
  // the workspace's own contact-picker already uses) — this is that
  // same two-call sequence, just started from the contact's own side.
  const createOfferForContact = async () => {
    if (!id) return
    setCreatingOffer(true)
    setCreateError(null)
    try {
      // A retry after a failed POST replays the offer the first POST
      // created instead of making a second one, then sets its contact. If
      // the PATCH landed and only its response was lost, the retry's PATCH
      // is a version conflict until the screen is opened again: no second
      // offer either way.
      const created = await api.post<SalesOfferRead>('/sales/offers', undefined, offerCreateKey.headers([id]))
      const updated = await api.patch<SalesOfferRead>(
        `/sales/offers/${created.id}`,
        { contactId: id },
        { 'If-Match': String(created.version) }
      )
      offerCreateKey.renew()
      navigate(`/sales/offers/${updated.id}`)
    } catch (err) {
      // KAN-273 (ADR-035): said, not dropped; the retry carries the key.
      setCreateError(apiRefusalText(t, err, 'contactDetail.errors.createOfferFailed'))
    } finally {
      setCreatingOffer(false)
    }
  }

  // KAN-58 — the contact→contract entry point. Simpler than the offer
  // flow above: ContractCreate accepts contactId directly, so this is one
  // POST, not POST-then-PATCH.
  const createContractForContact = async () => {
    if (!id) return
    setCreatingContract(true)
    setCreateError(null)
    try {
      const body = { contactId: id }
      const created = await api.post<SalesContractRead>('/sales/contracts', body, contractCreateKey.headers(body))
      contractCreateKey.renew()
      navigate(`/sales/contracts/${created.id}`)
    } catch (err) {
      setCreateError(apiRefusalText(t, err, 'contactDetail.errors.createContractFailed'))
    } finally {
      setCreatingContract(false)
    }
  }

  // KAN-14 / FR-22 / FR-12 — toggles the existing `lifecycleStatus` field
  // (already a plain PATCH-able field, `saveField` above) rather than a
  // new endpoint. Reverts to `active` when unset — ADR-065's own
  // distinction is that do-not-contact stops contact, never credit, and
  // is always reversible, unlike merge/anonymise.
  const toggleDoNotContact = () => {
    void saveField({ lifecycleStatus: contact?.lifecycleStatus === 'do_not_contact' ? 'active' : 'do_not_contact' })
  }

  const reload = () => {
    void contactQuery.refetch()
  }

  // FR-07: phone/email rows have no version column ("is_primary isn't a
  // high-contention field") — no If-Match here, unlike saveField above.
  // Every mutation still writes an audit event server-side (phone_add,
  // phone_remove, ...), so history needs the same invalidation.
  const invalidateContact = (kind: 'phones' | 'emails') => {
    const createKey = kind === 'phones' ? phoneKey : emailKey
    createKey.renew()
    void queryClient.invalidateQueries({ queryKey: ['customer', id, kind] })
    void queryClient.invalidateQueries({ queryKey: ['customer', id, 'history'] })
  }

  const createPhone = async (row: { type: PhoneType; value: string }) => {
    const path = `/contacts/${id}/phones`
    const body = { phoneType: row.type, phoneE164: row.value }
    await api.post<ContactPhoneRead>(path, body, phoneKey.headers([path, body]))
    invalidateContact('phones')
  }
  const updatePhone = async (phoneId: string, patch: ContactPointUpdatePatch<PhoneType>) => {
    await api.patch<ContactPhoneRead>(`/contacts/${id}/phones/${phoneId}`, {
      phoneType: patch.type,
      label: patch.label,
      phoneE164: patch.value,
      isPrimary: patch.isPrimary,
      validTo: patch.validTo,
      doNotUse: patch.doNotUse,
      doNotUseReason: patch.doNotUseReason,
      consentGranted: patch.consentGranted,
      consentScope: patch.consentScope,
      consentSource: patch.consentSource,
    })
    invalidateContact('phones')
  }
  const deletePhone = async (phoneId: string) => {
    await api.delete(`/contacts/${id}/phones/${phoneId}`)
    invalidateContact('phones')
  }

  const createEmail = async (row: { type: EmailType; value: string }) => {
    const path = `/contacts/${id}/emails`
    const body = { emailType: row.type, emailAddress: row.value }
    await api.post<ContactEmailRead>(path, body, emailKey.headers([path, body]))
    invalidateContact('emails')
  }
  const updateEmail = async (emailId: string, patch: ContactPointUpdatePatch<EmailType>) => {
    await api.patch<ContactEmailRead>(`/contacts/${id}/emails/${emailId}`, {
      emailType: patch.type,
      label: patch.label,
      emailAddress: patch.value,
      isPrimary: patch.isPrimary,
      validTo: patch.validTo,
      doNotUse: patch.doNotUse,
      doNotUseReason: patch.doNotUseReason,
      consentGranted: patch.consentGranted,
      consentScope: patch.consentScope,
      consentSource: patch.consentSource,
    })
    invalidateContact('emails')
  }
  const deleteEmail = async (emailId: string) => {
    await api.delete(`/contacts/${id}/emails/${emailId}`)
    invalidateContact('emails')
  }

  // D-08: write is platform_admin-only (see app/models/contact.py's
  // ContactExternalId docstring) — no version column either, same
  // no-If-Match shape as the contact-point handlers above.
  const invalidateExternalIds = () => {
    externalIdKey.renew()
    void queryClient.invalidateQueries({ queryKey: ['customer', id, 'external-ids'] })
    void queryClient.invalidateQueries({ queryKey: ['customer', id, 'history'] })
  }

  const createExternalId = async (row: { systemName: string; externalId: string }) => {
    const path = `/contacts/${id}/external-ids`
    const body = { systemName: row.systemName, externalId: row.externalId }
    await api.post<ContactExternalIdRead>(path, body, externalIdKey.headers([path, body]))
    invalidateExternalIds()
  }
  const updateExternalId = async (rowId: string, patch: { systemName?: string; externalId?: string }) => {
    await api.patch<ContactExternalIdRead>(`/contacts/${id}/external-ids/${rowId}`, {
      systemName: patch.systemName,
      externalId: patch.externalId,
    })
    invalidateExternalIds()
  }
  const deleteExternalId = async (rowId: string) => {
    await api.delete(`/contacts/${id}/external-ids/${rowId}`)
    invalidateExternalIds()
  }

  const dealRows = useMemo(
    () => toContactDealRows(offersQuery.data?.items ?? [], contractsQuery.data?.items ?? []),
    [offersQuery.data, contractsQuery.data]
  )

  // KAN-336 — the History tab renders a country code as its name, from the
  // same reference list the Overview uses.
  const historyCountryLabels = useMemo(
    () => Object.fromEntries(countryOptions.map((option) => [option.value, option.label])),
    [countryOptions],
  )
  // KAN-336 — the History tab renders an id as the label the contact record
  // carries for it: the advisor's stored name, a linked vehicle's make and model.
  const historyIdLabels = useMemo(() => {
    // KAN-371: the History endpoint names the ids its events carry (a Betrieb,
    // a user, another contact, a vehicle); the record's own labels follow.
    const labels: Record<string, string> = { ...(historyQuery.data?.labels ?? {}) }
    for (const party of vehiclesQuery.data?.items ?? []) {
      labels[party.vehicleId] = vehicleLabel(party.vehicle, t('contactDetail.vehicles.unknownVehicle'))
    }
    if (contact?.advisorId && contact.advisorLabel) labels[contact.advisorId] = contact.advisorLabel
    return labels
  }, [historyQuery.data?.labels, vehiclesQuery.data, contact?.advisorId, contact?.advisorLabel, t])

  const tabs: DetailTab[] = useMemo(
    () => [
      { id: 'overview', label: t('contactDetail.tabs.overview') },
      // KAN-49 — the query now fetches include_closed=true (historical
      // roles must render, FR-19), but the tab badge should still count
      // only currently-active relationships, not "3" for a contact whose
      // 3 vehicle relationships have all since ended.
      {
        id: 'vehicles',
        label: t('contactDetail.tabs.vehicles'),
        // KAN-644: a role ending later (until a planned handover) still counts.
        count: vehiclesQuery.data?.items.filter((v) => holdsNowOrLater(v)).length,
      },
      ...(salesEntitled
        ? [
            {
              id: 'offersContracts',
              label: t('contactDetail.tabs.offersContracts'),
              count: offersQuery.data && contractsQuery.data ? dealRows.length : undefined,
            },
          ]
        : []),
      { id: 'history', label: t('contactDetail.tabs.history'), count: historyQuery.data?.items.length },
      { id: 'external-ids', label: t('contactDetail.tabs.externalIds'), count: externalIdsQuery.data?.items.length },
    ],
    [t, vehiclesQuery.data, salesEntitled, offersQuery.data, contractsQuery.data, dealRows.length, historyQuery.data, externalIdsQuery.data]
  )

  if (contactQuery.isLoading) return <Loader />
  if (contactQuery.isError || !contact) {
    return (
      <Alert color="red" title={t('contactDetail.header.failedToLoad')}>
        {apiRefusalText(t, contactQuery.error, 'contactDetail.errors.somethingWentWrong')}
      </Alert>
    )
  }

  // KAN-44 / FR-22 (ADR-061) — the header's primary/alternative/overflow
  // and the contact list's row menu both render from this one builder, so
  // "New offer" / "New contract" can never carry a different enabled state
  // or reason on the two surfaces. FR-14 anonymisation stays out (not
  // built). KAN-58 wires "New contract" for real, on both surfaces.
  const onNewOffer = salesEntitled ? () => void createOfferForContact() : undefined
  const rowMenu = buildContactRowMenu(t, contact, {
    onNewOffer,
    onNewContract: salesEntitled ? () => void createContractForContact() : undefined,
    onCopyContactNumber: () => void navigator.clipboard.writeText(contact.contactNumber),
    onToggleDoNotContact: toggleDoNotContact,
    onManageCreditBlock: () => setCreditBlockOpen(true),
    onMergeInto: () => setMergeModalOpen(true),
    onLinkVehicle: () => setLinkVehicleOpen(true),
  }, { mayWriteCustomerRole })

  // buildContactRowMenu has no concept of an in-flight request (it's a
  // pure function of contact business state, shared with the list's row
  // menu, which tracks no per-row "creating" state at all). The header's
  // primary — «New offer» (KAN-468) — merges `creatingOffer` in by hand for
  // the same reason — this does the equivalent for "New contract" inside the
  // overflow, so a slow POST /sales/contracts can't be double-fired by
  // reopening the "..." menu before the first request's navigate() lands.
  const overflowActions = {
    ...rowMenu.overflow,
    createFrom: rowMenu.overflow.createFrom?.map((action) =>
      action.label === t('contactRowMenu.newContract') ? { ...action, disabled: action.disabled || creatingContract } : action
    ),
  }

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 20 }}>
      <DetailHeader
        labels={{ moreActions: t('common.moreActions') }}
        entityMark={contact.contactType === 'business' ? <Building2 size={24} /> : <User size={24} />}
        title={label ?? contact.contactNumber}
        businessKey={contact.contactNumber}
        badges={
          <>
            <ContactTypeBadge type={contact.contactType} label={translatedContactTypeLabel(t, contact.contactType)} />
            {/* KAN-464: the roles the contact holds; the lifecycle status only where it means something (Q1). */}
            {(contact.roles ?? []).map((role) => (
              <Badge key={role} tone="informational">
                {translatedRoleLabel(t, role)}
              </Badge>
            ))}
            {showsLifecycleStatus(contact) && (
              <LifecycleStatusBadge status={contact.lifecycleStatus} label={translatedLifecycleLabel(t, contact.lifecycleStatus)} />
            )}
            <LanguageBadge language={contact.language} />
            {(contact.contactGroups ?? []).map((code) => (
              <Badge key={code} tone="purple">
                {contactGroups.label(code)}
              </Badge>
            ))}
          </>
        }
        primaryAction={
          rowMenu.primary && {
            ...rowMenu.primary,
            disabled: rowMenu.primary.disabled || (rowMenu.primary.onClick === onNewOffer && creatingOffer),
          }
        }
        alternativeAction={rowMenu.alternative}
        overflowActions={overflowActions}
      />

      {createError && (
        <Alert color="red" role="alert" withCloseButton closeButtonLabel={t('common.dismiss')} onClose={() => setCreateError(null)}>
          {createError}
        </Alert>
      )}

      <DetailTabs tabs={tabs} activeTab={activeTab} onTabChange={setActiveTab} />

      {activeTab === 'overview' && (
        <OverviewTab
          contact={contact}
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
          countryOptions={countryOptions}
          locale={locale}
        />
      )}
      {activeTab === 'vehicles' && (
        <VehiclesTab
          vehicles={vehiclesQuery.data?.items ?? []}
          loading={vehiclesQuery.isLoading}
          error={vehiclesQuery.isError ? t('contactDetail.errors.failedToLoadVehicles') : null}
          locale={locale}
          onAdd={() => setLinkVehicleOpen(true)}
          canEndEntry={canEndVehicleEntry}
          onEndEntry={endVehicleEntry}
        />
      )}
      {activeTab === 'offersContracts' && (
        <OffersContractsTab
          offers={offersQuery.data?.items ?? []}
          contracts={contractsQuery.data?.items ?? []}
          loading={offersQuery.isLoading || contractsQuery.isLoading}
          error={
            offersQuery.isError || contractsQuery.isError
              ? t('contactDetail.errors.failedToLoadOffersContracts')
              : null
          }
          locale={locale}
          onNewOffer={() => void createOfferForContact()}
          newOfferDisabled={creatingOffer || contact.lifecycleStatus === 'do_not_contact'}
          newOfferDisabledReason={
            contact.lifecycleStatus === 'do_not_contact'
              // Was 'contactDetail.header.newOfferDisabledReason', a key
              // that was never defined in any locale bundle (⚠ MISSING
              // I18N KEY at runtime) — reusing the row menu's own key,
              // which says the same thing and actually exists.
              ? t('contactRowMenu.newOfferDisabledDoNotContact')
              : undefined
          }
          // KAN-58 — the empty-state "New contract" button, mirroring "New
          // offer" above. Same do-not-contact→credit-block precedence as
          // buildContactRowMenu's own newContract (ADR-061: one row-menu
          // definition, but the disabled reason text is shared via i18n
          // keys rather than routed through the builder itself, since this
          // tab isn't a row-menu render).
          onNewContract={() => void createContractForContact()}
          newContractDisabled={creatingContract || contact.lifecycleStatus === 'do_not_contact' || contact.creditBlock}
          newContractDisabledReason={
            contact.lifecycleStatus === 'do_not_contact'
              ? t('contactRowMenu.newContractDisabledDoNotContact')
              : contact.creditBlock
                ? t('contactRowMenu.newContractDisabledBlocked', { reason: contact.creditBlockReason ?? '—' })
                : undefined
          }
        />
      )}
      {activeTab === 'history' && (
        <HistoryTab
          events={historyQuery.data?.items ?? []}
          loading={historyQuery.isLoading}
          error={
            historyQuery.isError
              ? apiRefusalText(t, historyQuery.error, 'contactDetail.errors.failedToLoadHistory')
              : null
          }
          locale={locale}
          idLabels={historyIdLabels}
          countryLabels={historyCountryLabels}
          contactGroupLabel={contactGroups.label}
        />
      )}
      {activeTab === 'external-ids' && (
        <ExternalIdsTab
          externalIds={externalIdsQuery.data?.items ?? []}
          loading={externalIdsQuery.isLoading}
          error={externalIdsQuery.isError ? t('contactDetail.errors.failedToLoadExternalIds') : null}
          canWrite={canWriteExternalIds}
          onCreate={createExternalId}
          onUpdate={updateExternalId}
          onDelete={deleteExternalId}
          locale={locale}
        />
      )}

      <MergeContactModal
        opened={mergeModalOpen}
        onClose={() => setMergeModalOpen(false)}
        contact={contact}
        phones={phonesQuery.data?.items ?? []}
        emails={emailsQuery.data?.items ?? []}
        onMerged={(survivorId) => {
          setMergeModalOpen(false)
          void queryClient.invalidateQueries({ queryKey: ['contacts'] })
          navigate(`/contacts/${survivorId}`)
        }}
      />

      {id && (
        <LinkVehicleModal
          opened={linkVehicleOpen}
          onClose={() => setLinkVehicleOpen(false)}
          contactId={id}
          onLinked={() => void queryClient.invalidateQueries({ queryKey: ['customer', id, 'vehicles'] })}
        />
      )}

      <CreditBlockDialog
        opened={creditBlockOpen}
        onClose={() => setCreditBlockOpen(false)}
        contact={contact}
        onSaved={(updated) => {
          queryClient.setQueryData(['customer', id], updated)
          void queryClient.invalidateQueries({ queryKey: ['customer', id, 'history'] })
        }}
      />
    </div>
  )
}
