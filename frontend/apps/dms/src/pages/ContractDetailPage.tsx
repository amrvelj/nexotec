import { useParams } from 'react-router-dom'
import { useQuery } from '@tanstack/react-query'
import { Alert, Loader } from '@mantine/core'
import { FileSignature } from 'lucide-react'
import { useTranslation } from 'react-i18next'
import { DetailHeader, useSetBreadcrumb } from '@nexotec/ui-kit'
import { api, ApiError } from '../api/client'
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

/** PR-1's minimal contract detail. Confirmation/reservation (PR-6), the
 * Preisaufbau/Beteiligte/Dokumente/Verlauf tabs and the offer-number
 * lineage business-key format ("C-001195 ← O-003216") extend this same
 * shell in later PRs.
 */
export function ContractDetailContent({ contractId: id, embedded = false }: ContractDetailContentProps) {
  const { t } = useTranslation()

  const contractQuery = useQuery({
    queryKey: ['sales-contract', id],
    queryFn: () => api.get<SalesContractRead>(`/sales/contracts/${id}`),
    enabled: Boolean(id),
  })

  useSetBreadcrumb(embedded ? null : [t('shell.nav.sales'), contractQuery.data?.contractNumber ?? id])

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

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 20 }}>
      <DetailHeader
        entityMark={<FileSignature size={24} />}
        title={contract.vehicleLabel ?? t('contractDetail.untitled')}
        businessKey={businessKey}
        badges={<></>}
      />
    </div>
  )
}
