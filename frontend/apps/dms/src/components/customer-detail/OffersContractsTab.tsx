import { useMemo } from 'react'
import { Link } from 'react-router-dom'
import { Button, Group } from '@mantine/core'
import { Handshake } from 'lucide-react'
import { useTranslation } from 'react-i18next'
import {
  DataGrid,
  SalesStatusBadge,
  SalesTypeBadge,
  type GridColumnDef,
  type SalesDealStatus,
} from '@nexotec/ui-kit'
import { useUiPreferencesContext } from '../../hooks/UiPreferencesContext'
import { formatDate, formatCurrencyChf } from '../../utils/format'
import { translatedSalesDealStatusLabel } from '../../salesOptions'
import { dataGridLabels } from '../../utils/dataGridI18n'
import type { SalesContractRead, SalesOfferRead } from '../../api/types'

/**
 * KAN-45 / FR-06 tab 3 — "the Sales overview grid embedded and filtered by
 * customerId". Columns and the offer/contract row link mirror `SalesListPage`
 * so this reads as the same grid, not a second table.
 *
 * Scope: the acting dealership's offers and contracts for this customer, not
 * the group's (`/v1/sales/*` scope on `principal.tenant_id`; Customer is
 * group-scoped, ADR-014). The group-wide view is the Phase C reporting
 * projection keyed by `group_id`.
 *
 * An offer that has since become a contract is shown once, as the contract —
 * the same one-row-per-deal-lineage rule the Sales overview's `sales_deal`
 * projection applies.
 */
export interface CustomerDealRow {
  id: string
  entityType: 'offer' | 'contract'
  number: string
  status: SalesDealStatus
  customerLabel: string | null
  vehicleLabel: string | null
  grossPrice: string | null
  margin: string | null
  updatedAt: string
  href: string
}

function offerRow(o: SalesOfferRead): CustomerDealRow {
  return {
    id: o.id,
    entityType: 'offer',
    number: o.offerNumber,
    status: o.status as SalesDealStatus,
    customerLabel: o.customerLabel,
    vehicleLabel: o.vehicleLabel,
    grossPrice: o.grossPrice,
    margin: o.margin,
    updatedAt: o.updatedAt,
    href: `/sales/offers/${o.id}`,
  }
}

function contractRow(c: SalesContractRead): CustomerDealRow {
  return {
    id: c.id,
    entityType: 'contract',
    number: c.contractNumber,
    status: c.status as SalesDealStatus,
    customerLabel: c.customerLabel,
    vehicleLabel: c.vehicleLabel,
    grossPrice: c.grossPrice,
    margin: c.margin,
    updatedAt: c.updatedAt,
    href: `/sales/contracts/${c.id}`,
  }
}

/** Offers + contracts as one deal list, newest first, an offer that became a
 * contract folded into its contract row. Exported for the count on the tab. */
export function toCustomerDealRows(
  offers: SalesOfferRead[],
  contracts: SalesContractRead[],
): CustomerDealRow[] {
  const supersededOfferIds = new Set(contracts.map((c) => c.offerId).filter((id): id is string => id != null))
  const rows = [
    ...offers.filter((o) => !supersededOfferIds.has(o.id)).map(offerRow),
    ...contracts.map(contractRow),
  ]
  return rows.sort((a, b) => (a.updatedAt < b.updatedAt ? 1 : a.updatedAt > b.updatedAt ? -1 : 0))
}

export function OffersContractsTab({
  offers,
  contracts,
  loading,
  error,
  locale,
  onNewOffer,
  newOfferDisabled,
  newOfferDisabledReason,
  onNewContract,
  newContractDisabled,
  newContractDisabledReason,
}: {
  offers: SalesOfferRead[]
  contracts: SalesContractRead[]
  loading: boolean
  error: string | null
  locale: string
  onNewOffer: () => void
  newOfferDisabled?: boolean
  newOfferDisabledReason?: string
  // KAN-58 — the empty-state's own "New contract" entry point, mirroring
  // "New offer" above (previously this tab's empty state had no contract
  // path at all — flagged during KAN-58's plan and folded into its scope).
  onNewContract: () => void
  newContractDisabled?: boolean
  newContractDisabledReason?: string
}) {
  const { t } = useTranslation()
  const { density } = useUiPreferencesContext()

  const rows = useMemo(() => toCustomerDealRows(offers, contracts), [offers, contracts])

  const columns: GridColumnDef<CustomerDealRow>[] = useMemo(
    () => [
      {
        id: 'number',
        header: t('salesList.columns.number'),
        cell: ({ row }) => row.original.number,
        meta: { mono: true },
      },
      {
        id: 'entityType',
        header: t('salesList.columns.type'),
        cell: ({ row }) => <SalesTypeBadge entityType={row.original.entityType} />,
      },
      {
        id: 'customerLabel',
        header: t('salesList.columns.customer'),
        cell: ({ row }) => row.original.customerLabel ?? '—',
      },
      {
        id: 'vehicleLabel',
        header: t('salesList.columns.vehicle'),
        cell: ({ row }) => row.original.vehicleLabel ?? '—',
      },
      {
        id: 'grossPrice',
        header: t('salesList.columns.grossPrice'),
        cell: ({ row }) =>
          row.original.grossPrice != null ? formatCurrencyChf(Number(row.original.grossPrice)) : '—',
        meta: { align: 'right' },
      },
      {
        id: 'status',
        header: t('salesList.columns.status'),
        cell: ({ row }) => (
          <SalesStatusBadge
            status={row.original.status}
            label={translatedSalesDealStatusLabel(t, row.original.status)}
          />
        ),
      },
      {
        id: 'margin',
        header: t('salesList.columns.margin'),
        cell: ({ row }) => (row.original.margin != null ? formatCurrencyChf(Number(row.original.margin)) : '—'),
        meta: { align: 'right' },
      },
      {
        id: 'updatedAt',
        header: t('salesList.columns.changed'),
        cell: ({ row }) => formatDate(row.original.updatedAt, locale),
        meta: { align: 'right' },
      },
    ],
    [t, locale],
  )

  return (
    <DataGrid<CustomerDealRow>
      columns={columns}
      rows={rows}
      getRowId={(row) => row.id}
      rowHref={(row) => row.href}
      linkComponent={Link}
      sort={[]}
      onSortChange={() => {}}
      density={density}
      loading={loading}
      fetchingNextPage={false}
      hasNextPage={false}
      onLoadMore={() => {}}
      error={error}
      total={rows.length}
      totalIsEstimate={false}
      isFiltered={false}
      locale={locale}
      labels={dataGridLabels(t)}
      emptyState={{
        icon: <Handshake size={24} />,
        title: t('customerDetail.offersContracts.emptyState.title'),
        description: t('customerDetail.offersContracts.emptyState.description'),
        action: (
          <Group gap="xs">
            <Button
              size="xs"
              onClick={onNewOffer}
              disabled={newOfferDisabled}
              title={newOfferDisabled ? newOfferDisabledReason : undefined}
            >
              {t('salesList.newOffer')}
            </Button>
            <Button
              size="xs"
              variant="outline"
              onClick={onNewContract}
              disabled={newContractDisabled}
              title={newContractDisabled ? newContractDisabledReason : undefined}
            >
              {t('customerRowMenu.newContract')}
            </Button>
          </Group>
        ),
      }}
    />
  )
}
