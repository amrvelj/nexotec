import { useParams } from 'react-router-dom'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import { Alert, Loader, Text } from '@mantine/core'
import { Ban, FileSignature } from 'lucide-react'
import { useTranslation } from 'react-i18next'
import { DetailHeader, SalesStatusBadge, SalesTypeBadge, StatRow, useSetBreadcrumb, type RowMenuGroups } from '@nexotec/ui-kit'
import { api, ApiError } from '../api/client'
import { translatedSalesDealStatusLabel } from '../salesOptions'
import { formatCurrencyChf } from '../utils/format'
import type { SalesContractRead } from '../api/types'

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
 */
export function ContractDetailContent({ contractId: id, embedded = false }: ContractDetailContentProps) {
  const { t } = useTranslation()
  const queryClient = useQueryClient()

  const contractQuery = useQuery({
    queryKey: ['sales-contract', id],
    queryFn: () => api.get<SalesContractRead>(`/sales/contracts/${id}`),
    enabled: Boolean(id),
  })

  useSetBreadcrumb(embedded ? null : [t('shell.nav.sales'), contractQuery.data?.contractNumber ?? id])

  const confirm = async () => {
    const contract = contractQuery.data
    if (!contract) return
    const updated = await api.post<SalesContractRead>(`/sales/contracts/${id}/confirm`, undefined, {
      'If-Match': String(contract.version),
    })
    queryClient.setQueryData(['sales-contract', id], updated)
  }

  const cancel = async () => {
    const contract = contractQuery.data
    if (!contract) return
    const reason = window.prompt(t('contractDetail.cancelReasonPrompt'))
    if (!reason) return
    const updated = await api.post<SalesContractRead>(
      `/sales/contracts/${id}/cancel`,
      { reason },
      { 'If-Match': String(contract.version) }
    )
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

  const overflowActions: RowMenuGroups = {
    navigate: [],
    destructive: [
      {
        label: t('contractDetail.actions.cancel'),
        icon: <Ban size={16} />,
        onClick: cancel,
        disabled: isTerminal,
        disabledReason: isTerminal ? t('contractDetail.actions.cancelDisabledReason') : undefined,
      },
    ],
  }

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 20 }}>
      <DetailHeader
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
          disabled: !isPending,
          disabledReason: !isPending ? t('contractDetail.actions.confirmDisabledReason') : undefined,
        }}
        overflowActions={overflowActions}
      />

      <StatRow
        stats={[
          { label: t('contractDetail.stats.grossPrice'), value: contract.grossPrice != null ? formatCurrencyChf(Number(contract.grossPrice)) : '—' },
          {
            label: t('contractDetail.stats.tradeIn'),
            value: contract.tradeInValue != null ? `− ${formatCurrencyChf(Number(contract.tradeInValue))}` : '—',
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
    </div>
  )
}
