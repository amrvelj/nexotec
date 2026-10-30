import { useMemo } from 'react'
import { Link } from 'react-router-dom'
import { Button, Group } from '@mantine/core'
import { Car, FileText, Plus, Warehouse } from 'lucide-react'
import { useTranslation } from 'react-i18next'
import { Badge, DataGrid, type GridColumnDef } from '@nexotec/ui-kit'
import { useUiPreferencesContext } from '../../hooks/UiPreferencesContext'
import { formatDate } from '../../utils/format'
import { translatedVehiclePartyRoleLabel } from '../../customerOptions'
import type { CustomerVehicleRead } from '../../api/types'
import { vehicleLabel } from './vehicleLabel'
import { useDataGridLabels } from '../../hooks/useDataGridLabels'

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
  const dataGridLabels = useDataGridLabels()
  const { density } = useUiPreferencesContext()

  const columns: GridColumnDef<CustomerVehicleRead>[] = useMemo(
    () => [
      { id: 'vin', header: t('customerDetail.vehicles.columns.vin'), cell: ({ row }) => row.original.vehicle.vin ?? '—', meta: { mono: true } },
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
              {vehicleLabel(row.original.vehicle, t('customerDetail.vehicles.unknownVehicle'))}
              {/* C-F (KAN-10, FR-C-15) — Customer 360 reaches a configuration
                  THROUGH THE VEHICLE: its Specification tab. A configuration
                  has no customer; a customer-keyed route would be a second
                  ownership link competing with VehicleParty. */}
              <Link
                to={`/vehicles/${row.original.vehicle.id}?tab=specification`}
                title={t('customerDetail.vehicles.specification')}
                aria-label={t('customerDetail.vehicles.specification')}
                style={{ display: 'inline-flex', color: 'inherit' }}
              >
                <FileText size={14} />
              </Link>
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
        // KAN-347: fixed like the role column beside it, so the label and its two icons are never clipped.
        meta: { width: 260 },
      },
      {
        id: 'role',
        header: t('customerDetail.vehicles.columns.role'),
        // KAN-347 (ADR-064): a row a customer merge kept closed at its own
        // start (KAN-177) is not an ordinary ended role — it says so beside
        // the role, and links to the customer it was merged into.
        cell: ({ row }) => {
          const { role, mergedIntoCustomerId } = row.original
          const label = translatedVehiclePartyRoleLabel(t, role)
          if (!mergedIntoCustomerId) return label
          return (
            <span style={{ display: 'inline-flex', alignItems: 'center', gap: 6 }}>
              {label}
              <Link to={`/customers/${mergedIntoCustomerId}`} title={t('customerDetail.vehicles.mergedIntoTitle')} style={{ display: 'inline-flex', textDecoration: 'none' }}>
                <Badge tone="slate">{t('customerDetail.vehicles.mergedInto')}</Badge>
              </Link>
            </span>
          )
        },
        // Wide enough for the longest role beside the merge badge (FR «Fusionné», DE «Zusammengeführt»).
        meta: { width: 240 },
      },
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
        labels={dataGridLabels}
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
