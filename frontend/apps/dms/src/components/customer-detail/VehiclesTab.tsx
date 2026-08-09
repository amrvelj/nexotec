import { useMemo } from 'react'
import { Car } from 'lucide-react'
import { useTranslation } from 'react-i18next'
import { DataGrid, type GridColumnDef } from '@nexotec/ui-kit'
import { useUiPreferencesContext } from '../../hooks/UiPreferencesContext'
import { formatDate } from '../../utils/format'
import { translatedVehiclePartyRoleLabel } from '../../customerOptions'
import { dataGridLabels } from '../../utils/dataGridI18n'
import type { CustomerVehicleRead } from '../../api/types'

// FR-06 Vehicles tab: "use the same data grid component as the overview
// screens." Per-customer relationship lists are small (no server-side
// pagination endpoint here), so this is a single fully-loaded page —
// hasNextPage is always false rather than wiring infinite scroll for a
// handful of rows.
export function VehiclesTab({
  vehicles,
  loading,
  error,
  locale,
}: {
  vehicles: CustomerVehicleRead[]
  loading: boolean
  error: string | null
  locale: string
}) {
  const { t } = useTranslation()
  const { density } = useUiPreferencesContext()

  const columns: GridColumnDef<CustomerVehicleRead>[] = useMemo(
    () => [
      { id: 'vin', header: t('customerDetail.vehicles.columns.vin'), cell: ({ row }) => row.original.vehicle.vin, meta: { mono: true } },
      {
        id: 'vehicle',
        header: t('customerDetail.vehicles.columns.vehicle'),
        cell: ({ row }) => `${row.original.vehicle.make} ${row.original.vehicle.model} (${row.original.vehicle.modelYear})`,
      },
      { id: 'role', header: t('customerDetail.vehicles.columns.role'), cell: ({ row }) => translatedVehiclePartyRoleLabel(t, row.original.role) },
      {
        id: 'effectiveFrom',
        header: t('customerDetail.vehicles.columns.since'),
        cell: ({ row }) => formatDate(row.original.effectiveFrom, locale),
        meta: { align: 'right' },
      },
      {
        id: 'effectiveTo',
        header: t('customerDetail.vehicles.columns.until'),
        cell: ({ row }) => (row.original.effectiveTo ? formatDate(row.original.effectiveTo, locale) : '—'),
        meta: { align: 'right' },
      },
    ],
    [t, locale]
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
      locale={locale}
      labels={dataGridLabels(t)}
      emptyState={{
        icon: <Car size={24} />,
        title: t('customerDetail.vehicles.emptyState.title'),
        description: t('customerDetail.vehicles.emptyState.description'),
      }}
    />
  )
}
