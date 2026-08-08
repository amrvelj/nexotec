import { useMemo } from 'react'
import { Receipt } from 'lucide-react'
import { Badge, DataGrid, type BadgeTone, type GridColumnDef } from '@nexotec/ui-kit'
import { useUiPreferencesContext } from '../../hooks/UiPreferencesContext'
import { formatDate } from '../../utils/format'
import type { TransactionRead, TransactionStatus, TransactionType } from '../../api/types'

const TYPE_LABEL: Record<TransactionType, string> = { sale: 'Sale', trade_in: 'Trade-in' }
// Same tone vocabulary as LifecycleStatusBadge: positive = success,
// terminal/neutral = slate, negative = destructive.
const STATUS_TONE: Record<TransactionStatus, BadgeTone> = { draft: 'slate', completed: 'success', cancelled: 'destructive' }
const STATUS_LABEL: Record<TransactionStatus, string> = { draft: 'Draft', completed: 'Completed', cancelled: 'Cancelled' }

export function TransactionsTab({ transactions, loading, error }: { transactions: TransactionRead[]; loading: boolean; error: string | null }) {
  const { density } = useUiPreferencesContext()

  const columns: GridColumnDef<TransactionRead>[] = useMemo(
    () => [
      { id: 'type', header: 'Type', cell: ({ row }) => TYPE_LABEL[row.original.transactionType] },
      {
        id: 'status',
        header: 'Status',
        cell: ({ row }) => <Badge tone={STATUS_TONE[row.original.status]}>{STATUS_LABEL[row.original.status]}</Badge>,
      },
      {
        id: 'amount',
        header: 'Amount',
        cell: ({ row }) => (row.original.amount ? `CHF ${Number(row.original.amount).toLocaleString('de-CH')}` : '—'),
        meta: { align: 'right', mono: true },
      },
      {
        id: 'transactionDate',
        header: 'Date',
        cell: ({ row }) => (row.original.transactionDate ? formatDate(row.original.transactionDate) : '—'),
        meta: { align: 'right' },
      },
      { id: 'externalRef', header: 'Reference', cell: ({ row }) => row.original.externalRef ?? '—' },
    ],
    []
  )

  return (
    <DataGrid<TransactionRead>
      columns={columns}
      rows={transactions}
      getRowId={(row) => row.id}
      sort={[]}
      onSortChange={() => {}}
      density={density}
      loading={loading}
      fetchingNextPage={false}
      hasNextPage={false}
      onLoadMore={() => {}}
      error={error}
      total={transactions.length}
      totalIsEstimate={false}
      isFiltered={false}
      emptyState={{ icon: <Receipt size={24} />, title: 'No transactions', description: 'This customer has no transactions on file.' }}
    />
  )
}
