import { useState } from 'react'
import { useParams } from 'react-router-dom'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import { Alert, Group, Loader, Stack, Text, Title } from '@mantine/core'
import { Ban, FileSignature } from 'lucide-react'
import { useTranslation } from 'react-i18next'
import {
  DetailHeader,
  SalesStatusBadge,
  SalesTypeBadge,
  StatRow,
  useOverlay,
  useSetBreadcrumb,
  type RowMenuGroups,
} from '@nexotec/ui-kit'
import { api, ApiError } from '../api/client'
import {
  DealCustomerContainer,
  DealPricingContainer,
  DealTradeInContainer,
  DealVehicleContainer,
  useDealCustomer,
} from '../components/deal-workspace/DealWorkspaceContainers'
import { SalesDocumentsSection } from '../components/SalesDocumentsSection'
import { RequestInvoiceDialog, type RequestInvoiceBody } from '../components/contract-detail/RequestInvoiceDialog'
import { DealOwnerSelect } from '../components/deal-workspace/DealOwnerSelect'
import { StockNumberLink } from '../components/deal-workspace/StockNumberLink'
import { buildDealTakeOverAction } from '../components/dealRowMenu'
import { useAuth } from '../auth/AuthContext'
import { CustomerDetailContent } from '../modules/customer'
import { StockDetailContent } from './StockDetailPage'
import { useIdempotencyKey } from '../hooks/useIdempotencyKey'
import { useDealSaveQueue } from '../hooks/useDealSaveQueue'
import { ValuationSourceMarker } from '../modules/valuation'
import { translatedSalesDealStatusLabel, translatedSalesEntityTypeLabel } from '../salesOptions'
import { formatCurrencyChf, formatDate } from '../utils/format'
import type { SalesContractRead, ValuationRead } from '../api/types'
import { apiRefusalText } from '../utils/apiRefusal'
import { toSwissLocale, type SupportedLanguage } from '../i18n'
import { VehicleChangedBanner } from '../components/deal-workspace/VehicleChangedBanner'

export function ContractDetailPage() {
  const { id } = useParams<{ id: string }>()
  if (!id) return null
  return <ContractDetailContent contractId={id} />
}

export interface ContractDetailContentProps {
  contractId: string
  /** § ADR-059 — true when rendered as an Overlay rather than the real
   * `/sales/contracts/:id` route. */
  embedded?: boolean
}

/**
 * FR-S contract detail — confirmed live: business-key lineage
 * ("C-001195 ← O-003216"), badges, primary "Vertrag bestätigen" /
 * overflow "Stornieren", the 4-stat hero row (Verkaufspreis /
 * Eintauschfahrzeug / Zu bezahlen / Marge). Preisaufbau/Beteiligte/
 * Dokumente/Verlauf tabs and the document print-preview (ADR-063) extend
 * this same shell in PR-7/8.
 *
 * KAN-67 (ADR-083) — while the contract is pending, the offer workspace's
 * own customer (since KAN-307, ADR-083 amended), vehicle, price build-up
 * and trade-in containers complete or adjust it in place. Confirming
 * freezes it: the containers are gone and the server refuses any edit. A
 * contract without a buyer cannot be confirmed (KAN-307).
 */
export function ContractDetailContent({ contractId: id, embedded = false }: ContractDetailContentProps) {
  const { t, i18n } = useTranslation()
  // KAN-405: «Übernehmen» takes the contract for the signed-in user.
  const currentUserId = useAuth().user?.id ?? null
  const queryClient = useQueryClient()
  const [confirmError, setConfirmError] = useState<string | null>(null)
  const [cancelError, setCancelError] = useState<string | null>(null)
  // A failed cancel keeps its reason: the next prompt offers it again, and
  // sent unchanged it is the same request under the same key.
  const [unsentCancelReason, setUnsentCancelReason] = useState<string | null>(null)
  // KAN-266 — one key per kind: a confirm retried after a lost response
  // gets its success replayed instead of a version conflict. Each success
  // renews its kind's key.
  const confirmKey = useIdempotencyKey()
  const cancelKey = useIdempotencyKey()
  const [editError, setEditError] = useState<string | null>(null)
  // KAN-288 — a typed price still waiting out its debounce, or a save on its
  // way to the server. Confirming meanwhile would sign the previous figure
  // and drop the typed one (the edit section unmounts on confirm), so the
  // primary action waits for both.
  const [pricingUnsaved, setPricingUnsaved] = useState(false)
  const overlay = useOverlay()

  const contractQuery = useQuery({
    queryKey: ['sales-contract', id],
    queryFn: () => api.get<SalesContractRead>(`/sales/contracts/${id}`),
    enabled: Boolean(id),
  })

  // KAN-101 — the trade-in figure is marked manual where it is (ADR-048).
  const tradeInValuationId = contractQuery.data?.tradeInValuationId ?? null
  const tradeInValuationQuery = useQuery({
    queryKey: ['valuation', tradeInValuationId],
    queryFn: () => api.get<ValuationRead>(`/valuations/${tradeInValuationId}`),
    enabled: tradeInValuationId != null,
  })

  useSetBreadcrumb(embedded ? null : [{ label: t('shell.nav.sales'), href: '/sales' }, contractQuery.data?.contractNumber ?? id])

  // KAN-307 — the buyer's record, for its do-not-contact state (the server
  // refuses that confirmation; the button says so first).
  const buyer = useDealCustomer(contractQuery.data?.customerId).data ?? null

  // KAN-288 / KAN-314 — every save (autosave PATCH, line items, trade-in)
  // runs through one queue: one after the other, each with the version the
  // previous one returned (the server locks the contract row on each), and
  // Confirm waits while any is queued or on its way. The queue reads the
  // contract from the query cache when a save is sent, so the configurator
  // overlay's closure sends the current version too.
  const saves = useDealSaveQueue<SalesContractRead>(
    () => queryClient.getQueryData<SalesContractRead>(['sales-contract', id]),
    (updated) => {
      setEditError(null)
      queryClient.setQueryData(['sales-contract', id], updated)
    }
  )

  // A refused edit is said in the user's language, keyed off the 409's
  // shape (never the backend's English message), and the page refetches so
  // it shows what the contract now is — confirmed in another tab, say.
  const editRefusalMessage = (err: unknown): string => {
    if (err instanceof ApiError && err.status === 409) {
      const details = (err.details ?? {}) as Record<string, unknown>
      if (details.currentVersion != null) return t('contractDetail.errors.editRefused.staleVersion')
      if (details.reason === 'contract_not_pending') return t('contractDetail.errors.editRefused.notPending')
      if (details.reason === 'contract_priced_without_inputs') return t('contractDetail.edit.pricedWithoutInputs')
    }
    // KAN-656: the buyer chosen was merged into another; the customer
    // container offers the survivor.
    if (err instanceof ApiError && err.status === 409 && err.details?.reason === 'customer_merged') {
      return t('offerWorkspace.customer.mergedRefused')
    }
    return t('contractDetail.errors.editRefused.generic')
  }

  const patchContract = async (patch: Record<string, unknown>) => {
    try {
      await saves.save((current) =>
        api.patch<SalesContractRead>(`/sales/contracts/${id}`, patch, { 'If-Match': String(current.version) })
      )
    } catch (err) {
      setEditError(editRefusalMessage(err))
      void contractQuery.refetch()
      throw err
    }
  }

  // Confirmation is refused (409) for a customer the dealership may not
  // contact, may not extend credit to (FR-S-24), or whose address it does
  // not have (D-20 / KAN-55); or the page is stale (If-Match version
  // conflict). The backend names a machine-readable `details.reason` for
  // the customer guards; the English message it also sends is never shown
  // — the localised sentence is built here, and an unknown 409 falls back
  // to the localised generic, never the backend string.
  const confirmRefusalMessage = (err: unknown): string => {
    if (!(err instanceof ApiError) || err.status !== 409) {
      return t('contractDetail.errors.confirmRefused.generic')
    }
    const details = (err.details ?? {}) as Record<string, unknown>
    if (details.currentVersion != null) {
      // check_version fires before the customer guards — a contract
      // confirmed / cancelled in another tab, not yet refetched here.
      void contractQuery.refetch()
      return t('contractDetail.errors.confirmRefused.staleVersion')
    }
    switch (details.reason) {
      case 'missing_customer':
        return t('contractDetail.errors.confirmRefused.missingCustomer')
      case 'missing_address':
        return t('contractDetail.errors.confirmRefused.missingAddress')
      case 'missing_vehicle':
        return t('contractDetail.errors.confirmRefused.missingVehicle')
      case 'missing_price':
        return t('contractDetail.errors.confirmRefused.missingPrice')
      case 'trade_in_valuation_expired':
        return t('contractDetail.errors.confirmRefused.tradeInValuationExpired')
      case 'vehicle_left_stock':
        return t('contractDetail.errors.confirmRefused.vehicleLeftStock')
      case 'do_not_contact':
        return t('contractDetail.errors.confirmRefused.doNotContact')
      case 'credit_block':
        return t('contractDetail.errors.confirmRefused.creditBlock', {
          reason: String(details.creditBlockReason ?? '—'),
        })
      case 'bad_status':
        return t('contractDetail.errors.confirmRefused.badStatus')
      default:
        return t('contractDetail.errors.confirmRefused.generic')
    }
  }

  // KAN-523 — «Rechnung anfordern», with the plate the car leaves with.
  const [invoiceDialogOpen, setInvoiceDialogOpen] = useState(false)
  const [invoiceNotice, setInvoiceNotice] = useState<string | null>(null)
  const requestInvoiceKey = useIdempotencyKey()
  const requestInvoice = async (body: RequestInvoiceBody | undefined) => {
    const contract = contractQuery.data
    if (!contract) return
    const path = `/sales/contracts/${id}/request-invoice`
    const updated = await api.post<SalesContractRead>(path, body, {
      'If-Match': String(contract.version),
      ...requestInvoiceKey.headers([path, body ?? null]),
    })
    requestInvoiceKey.renew()
    queryClient.setQueryData(['sales-contract', id], updated)
    setInvoiceNotice(
      body?.plate
        ? t('contractDetail.requestInvoice.requestedWithPlate', { plate: `${body.canton ?? ''} ${body.plate}`.trim() })
        : t('contractDetail.requestInvoice.requested'),
    )
  }

  const confirm = async () => {
    const contract = contractQuery.data
    if (!contract || pricingUnsaved || saves.pending > 0) return
    setConfirmError(null)
    try {
      const path = `/sales/contracts/${id}/confirm`
      const updated = await api.post<SalesContractRead>(path, undefined, {
        'If-Match': String(contract.version),
        ...confirmKey.headers([path]),
      })
      confirmKey.renew()
      queryClient.setQueryData(['sales-contract', id], updated)
    } catch (err) {
      setConfirmError(confirmRefusalMessage(err))
    }
  }

  // KAN-273 (ADR-035): a refused cancel says so, in the user's language like
  // a refused confirm (the backend's English message is never shown); a
  // stale page is refetched so the retry carries the current version.
  const cancelRefusalMessage = (err: unknown): string => {
    if (err instanceof ApiError && err.status === 409 && (err.details ?? {}).currentVersion != null) {
      void contractQuery.refetch()
      return t('contractDetail.errors.cancelRefused.staleVersion')
    }
    return t('contractDetail.errors.cancelRefused.generic')
  }

  const cancel = async () => {
    const contract = contractQuery.data
    if (!contract) return
    const reason = window.prompt(t('contractDetail.cancelReasonPrompt'), unsentCancelReason ?? undefined)
    if (!reason) return
    setCancelError(null)
    setUnsentCancelReason(reason)
    try {
      const path = `/sales/contracts/${id}/cancel`
      const body = { reason }
      const updated = await api.post<SalesContractRead>(path, body, {
        'If-Match': String(contract.version),
        ...cancelKey.headers([path, body]),
      })
      cancelKey.renew()
      setUnsentCancelReason(null)
      queryClient.setQueryData(['sales-contract', id], updated)
    } catch (err) {
      setCancelError(cancelRefusalMessage(err))
    }
  }

  if (contractQuery.isLoading) return <Loader />
  if (contractQuery.isError || !contractQuery.data) {
    return (
      <Alert color="red" title={t('contractDetail.errors.failedToLoad')}>
        {apiRefusalText(t, contractQuery.error, 'contractDetail.errors.somethingWentWrong')}
      </Alert>
    )
  }

  const contract = contractQuery.data
  const businessKey = contract.offerNumber ? `${contract.contractNumber} ← ${contract.offerNumber}` : contract.contractNumber
  const isPending = contract.status === 'pending'
  const isTerminal = contract.status === 'cancelled' || contract.status === 'invoiced'
  // KAN-66 (G-67) — a contract born from a customer with no offer
  // (KAN-58) has no vehicle and no price. Mirror the backend's own
  // completeness guard here (ADR-061: don't invite a click that will only
  // be refused) rather than waiting for the 409 to say so.
  // CHF 0.00 counts as a price (Anto, 2026-10-10, KAN-67) — only an empty
  // one is refused, as on the server.
  const isEmpty = contract.vehicleSource == null || contract.grossPrice == null
  // KAN-307 (Anto, 2026-10-12) — never signed without a buyer; mirrors the
  // server's `missing_customer` refusal.
  const hasNoBuyer = contract.customerId == null
  const buyerDoNotContact = buyer?.lifecycleStatus === 'do_not_contact'
  // A contract priced before contracts kept their pricing inputs: its
  // figures stand, but the server refuses to rebuild them (409
  // contract_priced_without_inputs) — say so instead of offering editors;
  // only its buyer can still be picked, when it has none (KAN-315).
  const pricedWithoutInputs = contract.vehicleSnapshotFrozenAt == null && contract.grossPrice != null
  const isSaving = pricingUnsaved || saves.pending > 0
  const confirmDisabledReason = !isPending
    ? t('contractDetail.actions.confirmDisabledReason')
    : hasNoBuyer
      ? t('contractDetail.actions.confirmDisabledReasonNoBuyer')
      : buyerDoNotContact
        ? t('contractDetail.errors.confirmRefused.doNotContact')
        : isEmpty
          ? t('contractDetail.actions.confirmDisabledReasonEmpty')
          : isSaving
            ? t('contractDetail.actions.confirmDisabledReasonSaving')
            : undefined

  // KAN-523 — a confirmed contract's primary action is «Rechnung anfordern»
  // (ADR-061: one primary action); the server's gates are mirrored so the
  // click is never invited only to be refused.
  const isConfirmed = contract.status === 'confirmed'
  // KAN-539 — once per contract: after the request the action stays visible,
  // disabled, and names the day it was done.
  const requestInvoiceDisabledReason = !isConfirmed
    ? t('contractDetail.actions.requestInvoiceDisabledNotConfirmed')
    : contract.invoiceRequestedAt
      ? t('contractDetail.actions.requestInvoiceDisabledAlreadyRequested', {
          date: formatDate(contract.invoiceRequestedAt, toSwissLocale(i18n.language as SupportedLanguage)),
        })
      : contract.isLegacy
        ? t('contractDetail.actions.requestInvoiceDisabledLegacy')
        : !contract.isInvoiceable
          ? t('contractDetail.actions.requestInvoiceDisabledNotPurchased')
          : undefined
  const runPrimaryAction = () => {
    if (isConfirmed) {
      setInvoiceNotice(null)
      setInvoiceDialogOpen(true)
      return
    }
    void confirm()
  }

  const containerProps = {
    ownerType: 'contract' as const,
    deal: contract,
    patch: patchContract,
    save: saves.save,
  }
  // ADR-059 — the buyer's and the stock record open over the contract, never
  // instead of it.
  const openCustomerOverlay = () => {
    if (!contract.customerId) return
    overlay.push({
      key: `customer-overlay-${contract.customerId}`,
      content: <CustomerDetailContent customerId={contract.customerId} embedded />,
    })
  }
  const openStockItemOverlay = () => {
    if (!contract.stockItemId) return
    overlay.push({
      key: `stock-overlay-${contract.stockItemId}`,
      content: <StockDetailContent stockItemId={contract.stockItemId} embedded />,
    })
  }

  // KAN-405: «Übernehmen» — the deal's one definition, shared with the Sales
  // list's row menu (ADR-061); sent through this page's save queue.
  const takeOverAction = buildDealTakeOverAction(t, { entityType: 'contract', ...contract }, currentUserId, () => {
    if (currentUserId) void patchContract({ ownerUserId: currentUserId }).catch(() => {})
  })
  // KAN-405: the responsible seller while the contract is pending — also on
  // one priced without inputs, whose seller alone may change (Anto, 2026-10-28).
  const ownerSelect = <DealOwnerSelect deal={contract} onChange={(ownerUserId) => patchContract({ ownerUserId })} />

  const overflowActions: RowMenuGroups = {
    navigate: [],
    edit: [takeOverAction],
    destructive: [
      {
        label: t('contractDetail.actions.cancel'),
        icon: <Ban size={16} />,
        onClick: cancel,
        // KAN-147 — a sale migrated from the legacy system is history; the
        // backend refuses its cancellation (409 legacy_sale).
        disabled: isTerminal || contract.isLegacy,
        disabledReason: isTerminal
          ? t('contractDetail.actions.cancelDisabledReason')
          : contract.isLegacy
            ? t('contractDetail.actions.cancelDisabledReasonLegacy')
            : undefined,
      },
    ],
  }

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 20 }}>
      {confirmError && (
        <Alert color="red" title={t('contractDetail.errors.confirmRefused.title')} withCloseButton closeButtonLabel={t('common.dismiss')} onClose={() => setConfirmError(null)}>
          {confirmError}
        </Alert>
      )}
      {cancelError && (
        <Alert color="red" role="alert" title={t('contractDetail.errors.cancelRefused.title')} withCloseButton closeButtonLabel={t('common.dismiss')} onClose={() => setCancelError(null)}>
          {cancelError}
        </Alert>
      )}

      {invoiceNotice && (
        <Alert color="green" role="status" withCloseButton closeButtonLabel={t('common.dismiss')} onClose={() => setInvoiceNotice(null)}>
          {invoiceNotice}
        </Alert>
      )}

      <DetailHeader
        labels={{ moreActions: t('common.moreActions') }}
        entityMark={<FileSignature size={24} />}
        title={contract.vehicleLabel ?? t('contractDetail.untitled')}
        businessKey={businessKey}
        badges={
          <>
            <SalesTypeBadge entityType="contract" label={translatedSalesEntityTypeLabel(t, 'contract')} />
            <SalesStatusBadge status={contract.status as never} label={translatedSalesDealStatusLabel(t, contract.status as never)} />
            {/* KAN-570: beside the car (the title) once the vehicle container,
                which shows it while the contract is edited, is gone. */}
            {!(isPending && !pricedWithoutInputs) && contract.stockItemId && contract.stockItemLabel && (
              <StockNumberLink stockNumber={contract.stockItemLabel} onOpen={openStockItemOverlay} />
            )}
          </>
        }
        primaryAction={{
          label: isConfirmed ? t('contractDetail.actions.requestInvoice') : t('contractDetail.actions.confirm'),
          onClick: runPrimaryAction,
          disabled: isConfirmed
            ? requestInvoiceDisabledReason !== undefined
            : !isPending || hasNoBuyer || buyerDoNotContact || isEmpty || isSaving,
          disabledReason: isConfirmed ? requestInvoiceDisabledReason : confirmDisabledReason,
        }}
        overflowActions={overflowActions}
      />

      <StatRow
        stats={[
          { label: t('contractDetail.stats.grossPrice'), value: contract.grossPrice != null ? formatCurrencyChf(Number(contract.grossPrice)) : '—' },
          {
            label: t('contractDetail.stats.tradeIn'),
            value:
              contract.tradeInValue != null ? (
                <Group gap={6} component="span">
                  <span style={{ whiteSpace: 'nowrap' }}>{`− ${formatCurrencyChf(Number(contract.tradeInValue))}`}</span>
                  {tradeInValuationQuery.data && <ValuationSourceMarker source={tradeInValuationQuery.data.source} />}
                </Group>
              ) : (
                '—'
              ),
            negative: contract.tradeInValue != null,
          },
          { label: t('contractDetail.stats.payable'), value: contract.payable != null ? formatCurrencyChf(Number(contract.payable)) : '—' },
          {
            label: t('contractDetail.stats.margin'),
            value: contract.margin != null ? formatCurrencyChf(Number(contract.margin)) : '—',
          },
        ]}
      />

      {contract.financing && <Text size="sm" c="dimmed">{t('contractDetail.financing')}: {t(`salesEnums.financing.${contract.financing}`)}</Text>}

      {/* KAN-315 (Anto, 2026-10-12) — its price stays as it stands, but one
          without a buyer may be given one: the customer container alone,
          never the vehicle, price or trade-in. The server takes a PATCH of
          the customer only, for a contract that has none. */}
      {isPending && pricedWithoutInputs && (
        <Stack gap="md" data-testid="contract-priced-without-inputs">
          <Alert color="gray">
            {hasNoBuyer ? t('contractDetail.edit.pricedWithoutInputsNoBuyer') : t('contractDetail.edit.pricedWithoutInputs')}
          </Alert>
          {editError && (
            <Alert color="red" withCloseButton closeButtonLabel={t('common.dismiss')} onClose={() => setEditError(null)}>
              {editError}
            </Alert>
          )}
          {ownerSelect}
          <DealCustomerContainer ownerType="contract" deal={contract} patch={patchContract} onOpenCustomer={openCustomerOverlay} />
        </Stack>
      )}

      {/* KAN-553 (ADR-115): the stock car changed after this contract was priced. */}
      {isPending && (
        <VehicleChangedBanner
          vehicleChangedAt={contract.vehicleChangedAt}
          vehicleChangedFields={contract.vehicleChangedFields}
          busy={isSaving}
          {...(pricedWithoutInputs
            ? {}
            : {
                onRefresh: () => saves.save((current) => api.post<SalesContractRead>(`/sales/contracts/${id}/vehicle-refresh`, undefined, { 'If-Match': String(current.version) })),
                onDismiss: () => saves.save((current) => api.post<SalesContractRead>(`/sales/contracts/${id}/vehicle-refresh/dismiss`, undefined, { 'If-Match': String(current.version) })),
              })}
        />
      )}

      {isPending && !pricedWithoutInputs && (
        <Stack gap="md" data-testid="contract-edit">
          <div>
            <Title order={4}>{t('contractDetail.edit.title')}</Title>
            <Text size="sm" c="dimmed">{t('contractDetail.edit.hint')}</Text>
          </div>
          {editError && (
            <Alert color="red" withCloseButton closeButtonLabel={t('common.dismiss')} onClose={() => setEditError(null)}>
              {editError}
            </Alert>
          )}
          {ownerSelect}
          <DealCustomerContainer ownerType="contract" deal={contract} patch={patchContract} onOpenCustomer={openCustomerOverlay} />
          <DealVehicleContainer {...containerProps} onOpenStockItem={openStockItemOverlay} />
          <DealPricingContainer {...containerProps} onOpenStockItem={openStockItemOverlay} onUnsavedChange={setPricingUnsaved} />
          <DealTradeInContainer {...containerProps} />
        </Stack>
      )}

      <SalesDocumentsSection ownerType="contract" ownerId={id} />

      <RequestInvoiceDialog opened={invoiceDialogOpen} onClose={() => setInvoiceDialogOpen(false)} onRequest={requestInvoice} />
    </div>
  )
}
