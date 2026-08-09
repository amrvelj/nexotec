import { useMemo } from 'react'
import { Receipt } from 'lucide-react'
import { useTranslation } from 'react-i18next'
import { Badge, DataGrid, type BadgeTone, type GridColumnDef } from '@nexotec/ui-kit'
import { useUiPreferencesContext } from '../../hooks/UiPreferencesContext'
import { formatDate } from '../../utils/format'
import { translatedTransactionStatusLabel, translatedTransactionTypeLabel } from '../../customerOptions'
import { dataGridLabels } from '../../utils/dataGridI18n'
import type { TransactionRead, TransactionStatus } from '../../api/types'

// Same tone vocabulary as LifecycleStatusBadge: positive = success,
// terminal/neutral = slate, negative = destructive.
const STATUS_TONE: Record<TransactionStatus, BadgeTone> = { draft: 'slate', completed: 'success', cancelled: 'destructive' }

export function TransactionsTab({
  transactions,
  loading,
  error,
  locale,
}: {
  transactions: TransactionRead[]
  loading: boolean
  error: string | null
  locale: string
}) {
  const { t } = useTranslation()
  const { density } = useUiPreferencesContext()

  const columns: GridColumnDef<TransactionRead>[] = useMemo(
    () => [
      {
        id: 'type',
        header: t('customerDetail.transactions.columns.type'),
        cell: ({ row }) => translatedTransactionTypeLabel(t, row.original.transactionType),
      },
      {
        id: 'status',
        header: t('customerDetail.transactions.columns.status'),
        cell: ({ row }) => <Badge tone={STATUS_TONE[row.original.status]}>{translatedTransactionStatusLabel(t, row.original.status)}</Badge>,
      },
      {
        id: 'amount',
        header: t('customerDetail.transactions.columns.amount'),
        cell: ({ row }) => (row.original.amount ? `CHF ${Number(row.original.amount).toLocaleString(locale)}` : '—'),
        meta: { align: 'right', mono: true },
      },
      {
        id: 'transactionDate',
        header: t('customerDetail.transactions.columns.date'),
        cell: ({ row }) => (row.original.transactionDate ? formatDate(row.original.transactionDate, locale) : '—'),
        meta: { align: 'right' },
      },
      { id: 'externalRef', header: t('customerDetail.transactions.columns.reference'), cell: ({ row }) => row.original.externalRef ?? '—' },
    ],
    [t, locale]
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
      locale={locale}
      labels={dataGridLabels(t)}
      emptyState={{
        icon: <Receipt size={24} />,
        title: t('customerDetail.transactions.emptyState.title'),
        description: t('customerDetail.transactions.emptyState.description'),
      }}
    />
  )
}
