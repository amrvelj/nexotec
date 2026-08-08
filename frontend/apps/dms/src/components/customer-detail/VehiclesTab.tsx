import { useMemo } from 'react'
import { Car } from 'lucide-react'
import { DataGrid, type GridColumnDef } from '@nexotec/ui-kit'
import { useUiPreferencesContext } from '../../hooks/UiPreferencesContext'
import { formatDate } from '../../utils/format'
import type { CustomerVehicleRead, VehiclePartyRole } from '../../api/types'

const ROLE_LABEL: Record<VehiclePartyRole, string> = { owner: 'Owner', keeper: 'Keeper', driver: 'Driver' }

// FR-06 Vehicles tab: "use the same data grid component as the overview
// screens." Per-customer relationship lists are small (no server-side
// pagination endpoint here), so this is a single fully-loaded page —
// hasNextPage is always false rather than wiring infinite scroll for a
// handful of rows.
export function VehiclesTab({ vehicles, loading, error }: { vehicles: CustomerVehicleRead[]; loading: boolean; error: string | null }) {
  const { density } = useUiPreferencesContext()

  const columns: GridColumnDef<CustomerVehicleRead>[] = useMemo(
    () => [
      { id: 'vin', header: 'VIN', cell: ({ row }) => row.original.vehicle.vin, meta: { mono: true } },
      {
        id: 'vehicle',
        header: 'Vehicle',
        cell: ({ row }) => `${row.original.vehicle.make} ${row.original.vehicle.model} (${row.original.vehicle.modelYear})`,
      },
      { id: 'role', header: 'Role', cell: ({ row }) => ROLE_LABEL[row.original.role] },
      { id: 'effectiveFrom', header: 'Since', cell: ({ row }) => formatDate(row.original.effectiveFrom), meta: { align: 'right' } },
      {
        id: 'effectiveTo',
        header: 'Until',
        cell: ({ row }) => (row.original.effectiveTo ? formatDate(row.original.effectiveTo) : '—'),
        meta: { align: 'right' },
      },
    ],
    []
  )

  return (
    <DataGrid<CustomerVehicleRead>
      columns={columns}
      rows={vehicles}
      getRowId={(row) => row.id}
      sort={[]}
      onSortChange={() => {}}
      density={density}
      loading={loading}
      fetchingNextPage={false}
      hasNextPage={false}
      onLoadMore={() => {}}
      error={error}
      total={vehicles.length}
      totalIsEstimate={false}
      isFiltered={false}
      emptyState={{ icon: <Car size={24} />, title: 'No vehicles', description: 'This customer has no vehicle relationships on file.' }}
    />
  )
}
