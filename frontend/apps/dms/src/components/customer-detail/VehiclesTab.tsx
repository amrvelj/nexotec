import { useMemo } from 'react'
import { Link } from 'react-router-dom'
import { Button, Group } from '@mantine/core'
import { Car, Plus, Warehouse } from 'lucide-react'
import { useTranslation } from 'react-i18next'
import { DataGrid, type GridColumnDef } from '@nexotec/ui-kit'
import { useUiPreferencesContext } from '../../hooks/UiPreferencesContext'
import { formatDate } from '../../utils/format'
import { translatedVehiclePartyRoleLabel } from '../../customerOptions'
import { dataGridLabels } from '../../utils/dataGridI18n'
import type { CustomerVehicleRead } from '../../api/types'

// KAN-31: falls back to the vehicle number when the vehicle has no
// catalogue match — make/model/modelYear are null in that case (the
// common one today, not an edge case; see VehiclePartySummary's own
// doc). Never renders "null null (null)".
function vehicleLabel(vehicle: CustomerVehicleRead['vehicle']): string {
  if (vehicle.make && vehicle.model) {
    return vehicle.modelYear ? `${vehicle.make} ${vehicle.model} (${vehicle.modelYear})` : `${vehicle.make} ${vehicle.model}`
  }
  return vehicle.vehicleNumber
}

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
  onAdd,
}: {
  vehicles: CustomerVehicleRead[]
  loading: boolean
  error: string | null
  locale: string
  // KAN-31: the tab had no way to add a vehicle at all — the only path
  // was the detail header's overflow, easy to miss. Opens the same
  // LinkVehicleModal that overflow action already uses; never a second
  // implementation of "link a vehicle".
  onAdd: () => void
}) {
  const { t } = useTranslation()
  const { density } = useUiPreferencesContext()

  const columns: GridColumnDef<CustomerVehicleRead>[] = useMemo(
    () => [
      { id: 'vin', header: t('customerDetail.vehicles.columns.vin'), cell: ({ row }) => row.original.vehicle.vin, meta: { mono: true } },
      {
        id: 'vehicle',
        header: t('customerDetail.vehicles.columns.vehicle'),
        // KAN-49 / FR-19 amendment — a vehicle currently in the group's
        // own stock carries a link to its stock item, beside the vehicle
        // label (not its own column: it's a fact ABOUT this vehicle, same
        // idea as the roles, not another row-level attribute).
        cell: ({ row }) => {
          const { stockItem } = row.original
          return (
            <span style={{ display: 'inline-flex', alignItems: 'center', gap: 6 }}>
              {vehicleLabel(row.original.vehicle)}
              {stockItem && (
                <Link
                  to={`/stock/${stockItem.id}`}
                  title={t('customerDetail.vehicles.inStockTitle', { stockNumber: stockItem.stockNumber })}
                  style={{ display: 'inline-flex', color: 'inherit' }}
                >
                  <Warehouse size={14} />
                </Link>
              )}
            </span>
          )
        },
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
      {
        // KAN-49 / FR-19 — the OTHER parties on this same car (owner=
        // leasing co, keeper=employer, driver=employee can all be true
        // at once). Renders nothing — not "—", not "null" — when there
        // are none (exit criterion 6), each name linking to that
        // customer's own record.
        id: 'otherParties',
        header: t('customerDetail.vehicles.columns.otherParties'),
        cell: ({ row }) => {
          const others = row.original.otherParties ?? []
          if (others.length === 0) return null
          return (
            <span style={{ display: 'inline-flex', flexWrap: 'wrap', gap: '4px 6px' }}>
              {others.map((party, index) => (
                // Keyed by customerId + role, not customerId alone: one
                // customer can legitimately hold two open roles at once
                // on the same vehicle (e.g. owner and keeper), which
                // would otherwise collide.
                <span key={`${party.customerId}-${party.role}`}>
                  {translatedVehiclePartyRoleLabel(t, party.role)}:{' '}
                  <Link to={`/customers/${party.customerId}`}>{party.displayName}</Link>
                  {index < others.length - 1 ? ' ·' : ''}
                </span>
              ))}
            </span>
          )
        },
      },
    ],
    [t, locale]
  )

  return (
    <>
      <Group justify="flex-end" mb="xs">
        <Button size="xs" variant="default" leftSection={<Plus size={14} />} onClick={onAdd}>
          {t('customerDetail.vehicles.addAction')}
        </Button>
      </Group>
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
          action: (
            <Button size="xs" onClick={onAdd}>
              {t('customerDetail.vehicles.addAction')}
            </Button>
          ),
        }}
      />
    </>
  )
}
