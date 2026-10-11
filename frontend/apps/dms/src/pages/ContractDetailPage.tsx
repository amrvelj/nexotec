import { useRef, useState } from 'react'
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
  DealPricingContainer,
  DealTradeInContainer,
  DealVehicleContainer,
} from '../components/deal-workspace/DealWorkspaceContainers'
import { SalesDocumentsSection } from '../components/SalesDocumentsSection'
import { StockDetailContent } from './StockDetailPage'
import { useIdempotencyKey } from '../hooks/useIdempotencyKey'
import { ValuationSourceMarker } from '../components/ValuationSourceMarker'
import { translatedSalesDealStatusLabel } from '../salesOptions'
import { formatCurrencyChf } from '../utils/format'
import type { SalesContractRead, ValuationRead } from '../api/types'

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
 * own vehicle, price build-up and trade-in containers complete or adjust
 * it in place (the customer is fixed). Confirming freezes it: the
 * containers are gone and the server refuses any edit.
 */
export function ContractDetailContent({ contractId: id, embedded = false }: ContractDetailContentProps) {
  const { t } = useTranslation()
  const queryClient = useQueryClient()
  const [confirmError, setConfirmError] = useState<string | null>(null)
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
  const [savesInFlight, setSavesInFlight] = useState(0)
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

  useSetBreadcrumb(embedded ? null : [t('shell.nav.sales'), contractQuery.data?.contractNumber ?? id])

  // The configurator overlay calls `patchContract` from a closure; reading
  // the contract through a ref keeps its If-Match current (same reason as
  // the offer workspace's own latestOffer ref).
  const latestContract = useRef<SalesContractRead | undefined>(undefined)
  latestContract.current = contractQuery.data
  const onContractUpdated = (updated: SalesContractRead) => {
    setEditError(null)
    queryClient.setQueryData(['sales-contract', id], updated)
  }

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
    return t('contractDetail.errors.editRefused.generic')
  }

  // KAN-288 — every save (autosave PATCH, line items, trade-in) runs
  // through here, so Confirm waits for whichever is still on its way.
  const trackSave = async <T,>(work: Promise<T>): Promise<T> => {
    setSavesInFlight((n) => n + 1)
    try {
      return await work
    } finally {
      setSavesInFlight((n) => n - 1)
    }
  }

  const patchContract = async (patch: Record<string, unknown>) => {
    const contract = latestContract.current
    if (!contract) return
    try {
      const updated = await trackSave(
        api.patch<SalesContractRead>(`/sales/contracts/${id}`, patch, { 'If-Match': String(contract.version) })
      )
      onContractUpdated(updated)
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

  const confirm = async () => {
    const contract = contractQuery.data
    if (!contract || pricingUnsaved || savesInFlight > 0) return
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

  const cancel = async () => {
    const contract = contractQuery.data
    if (!contract) return
    const reason = window.prompt(t('contractDetail.cancelReasonPrompt'))
    if (!reason) return
    const path = `/sales/contracts/${id}/cancel`
    const body = { reason }
    const updated = await api.post<SalesContractRead>(path, body, {
      'If-Match': String(contract.version),
      ...cancelKey.headers([path, body]),
    })
    cancelKey.renew()
    queryClient.setQueryData(['sales-contract', id], updated)
  }

  if (contractQuery.isLoading) return <Loader />
  if (contractQuery.isError || !contractQuery.data) {
    return (
      <Alert color="red" title={t('contractDetail.errors.failedToLoad')}>
        {contractQuery.error instanceof ApiError
          ? contractQuery.error.message
          : t('contractDetail.errors.somethingWentWrong')}
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
  // A contract priced before contracts kept their pricing inputs: its
  // figures stand, but the server refuses to rebuild them (409
  // contract_priced_without_inputs) — say so instead of offering editors.
  const pricedWithoutInputs = contract.vehicleSnapshotFrozenAt == null && contract.grossPrice != null
  const isSaving = pricingUnsaved || savesInFlight > 0
  const confirmDisabledReason = !isPending
    ? t('contractDetail.actions.confirmDisabledReason')
    : isEmpty
      ? t('contractDetail.actions.confirmDisabledReasonEmpty')
      : isSaving
        ? t('contractDetail.actions.confirmDisabledReasonSaving')
        : undefined

  const containerProps = {
    ownerType: 'contract' as const,
    deal: contract,
    getDeal: () => latestContract.current,
    patch: patchContract,
    onDealUpdated: onContractUpdated,
    trackSave,
  }
  // ADR-059 — the stock record opens over the contract, never instead of it.
  const openStockItemOverlay = () => {
    if (!contract.stockItemId) return
    overlay.push({
      key: `stock-overlay-${contract.stockItemId}`,
      content: <StockDetailContent stockItemId={contract.stockItemId} embedded />,
    })
  }

  const overflowActions: RowMenuGroups = {
    navigate: [],
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
        <Alert color="red" title={t('contractDetail.errors.confirmRefused.title')} withCloseButton onClose={() => setConfirmError(null)}>
          {confirmError}
        </Alert>
      )}

      <DetailHeader
        labels={{ moreActions: t('common.moreActions') }}
        entityMark={<FileSignature size={24} />}
        title={contract.vehicleLabel ?? t('contractDetail.untitled')}
        businessKey={businessKey}
        badges={
          <>
            <SalesTypeBadge entityType="contract" />
            <SalesStatusBadge status={contract.status as never} label={translatedSalesDealStatusLabel(t, contract.status as never)} />
          </>
        }
        primaryAction={{
          label: t('contractDetail.actions.confirm'),
          onClick: confirm,
          disabled: !isPending || isEmpty || isSaving,
          disabledReason: confirmDisabledReason,
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

      {isPending && pricedWithoutInputs && (
        <Alert color="gray" data-testid="contract-priced-without-inputs">
          {t('contractDetail.edit.pricedWithoutInputs')}
        </Alert>
      )}

      {isPending && !pricedWithoutInputs && (
        <Stack gap="md" data-testid="contract-edit">
          <div>
            <Title order={4}>{t('contractDetail.edit.title')}</Title>
            <Text size="sm" c="dimmed">{t('contractDetail.edit.hint')}</Text>
          </div>
          {editError && (
            <Alert color="red" withCloseButton onClose={() => setEditError(null)}>
              {editError}
            </Alert>
          )}
          <DealVehicleContainer {...containerProps} />
          <DealPricingContainer {...containerProps} onOpenStockItem={openStockItemOverlay} onUnsavedChange={setPricingUnsaved} />
          <DealTradeInContainer {...containerProps} />
        </Stack>
      )}

      <SalesDocumentsSection ownerType="contract" ownerId={id} />
    </div>
  )
}
