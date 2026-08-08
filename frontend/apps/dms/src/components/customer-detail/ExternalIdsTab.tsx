import { useMemo } from 'react'
import { Link2 } from 'lucide-react'
import { DataGrid, type GridColumnDef } from '@nexotec/ui-kit'
import { useUiPreferencesContext } from '../../hooks/UiPreferencesContext'
import { formatDate } from '../../utils/format'
import type { CustomerExternalIdRead } from '../../api/types'

export function ExternalIdsTab({ externalIds, loading, error }: { externalIds: CustomerExternalIdRead[]; loading: boolean; error: string | null }) {
  const { density } = useUiPreferencesContext()

  const columns: GridColumnDef<CustomerExternalIdRead>[] = useMemo(
    () => [
      { id: 'systemName', header: 'System', cell: ({ row }) => row.original.systemName },
      { id: 'externalId', header: 'External ID', cell: ({ row }) => row.original.externalId, meta: { mono: true } },
      { id: 'createdAt', header: 'Linked', cell: ({ row }) => formatDate(row.original.createdAt), meta: { align: 'right' } },
    ],
    []
  )

  return (
    <DataGrid<CustomerExternalIdRead>
      columns={columns}
      rows={externalIds}
      getRowId={(row) => row.id}
      sort={[]}
      onSortChange={() => {}}
      density={density}
      loading={loading}
      fetchingNextPage={false}
      hasNextPage={false}
      onLoadMore={() => {}}
      error={error}
      total={externalIds.length}
      totalIsEstimate={false}
      isFiltered={false}
      emptyState={{ icon: <Link2 size={24} />, title: 'No external IDs', description: 'No CRM/OEM system linkage on file for this customer.' }}
    />
  )
}
