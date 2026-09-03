import { useMemo, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { Alert, Badge, Button, Group, Stack, Title } from '@mantine/core'
import { useDebouncedValue } from '@mantine/hooks'
import { useQuery } from '@tanstack/react-query'
import { AlertTriangle, Car, Copy, ExternalLink } from 'lucide-react'
import { useTranslation } from 'react-i18next'
import { ActionBar, DataGrid, Picker, SelectionBar, useSetBreadcrumb, type GridColumnDef, type SortSpec } from '@nexotec/ui-kit'
import { useUiPreferencesContext } from '../hooks/UiPreferencesContext'
import { api } from '../api/client'
import { VehicleCreateDialog } from '../components/vehicle/VehicleCreateDialog'
import type { VehicleMdmRead, VehicleSearchResult } from '../api/types'

const GRID_KEY = 'mdm.vehicles.list'

/**
 * FR-V-06/FR-V-16: ONE search box on the vehicle list, and that one box
 * ALSO resolves identifiers. A Kontrollschild, VIN, Stammnummer or
 * vehicle number resolves and the hit is shown ABOVE the grid; anything
 * else filters the grid. There is deliberately no second search field
 * and no separate plate-lookup screen anywhere in this app — see the
 * WP-5 PR-9 commit message for why the prototype's old #/vehicles/lookup
 * nav entry was removed rather than kept alongside this.
 */
export function VehiclesListPage() {
  const { t } = useTranslation()
  useSetBreadcrumb([t('shell.nav.masterData'), t('shell.nav.vehicles')])
  const navigate = useNavigate()
  const { density, setDensity } = useUiPreferencesContext()

  const [query, setQuery] = useState('')
  const [debouncedQuery] = useDebouncedValue(query, 250)
  const [sort, setSort] = useState<SortSpec[]>([])
  const [createOpen, setCreateOpen] = useState(false)
  // KAN-7 — every other list grid (Customers already shipped; Sales gets
  // the same fix alongside this one) offers selection + a bulk action.
  // Vehicles had neither, which reads as an omission rather than a
  // decision. Same minimal, honest "copy IDs" action as Customers' own
  // precedent — proving the contract, not a real export feature this
  // screen doesn't own.
  const [selectedIds, setSelectedIds] = useState<Set<string>>(new Set())

  const searchQuery = useQuery({
    queryKey: [GRID_KEY, debouncedQuery],
    queryFn: () => api.get<VehicleSearchResult>(`/vehicle-mdm/search?q=${encodeURIComponent(debouncedQuery)}`),
  })

  const result = searchQuery.data
  const rows = result?.filtered.items ?? []

  const columns: GridColumnDef<VehicleMdmRead>[] = useMemo(
    () => [
      {
        id: 'vehicleNumber',
        header: t('vehiclesList.columns.vehicleNumber'),
        cell: ({ row }) => row.original.vehicleNumber,
        meta: { pinned: 'left', mono: true },
      },
      { id: 'vin', header: t('vehiclesList.columns.vin'), cell: ({ row }) => row.original.vin, meta: { mono: true } },
      {
        id: 'stammnummer',
        header: t('vehiclesList.columns.stammnummer'),
        cell: ({ row }) => row.original.stammnummer ?? <NotSet />,
      },
      {
        id: 'catalogueMatchStatus',
        header: t('vehiclesList.columns.matchStatus'),
        cell: ({ row }) => (
          <Badge variant="light" color={row.original.catalogueMatchStatus === 'matched' ? 'grape' : 'gray'}>
            {t(`vehiclesList.matchStatus.${row.original.catalogueMatchStatus}`)}
          </Badge>
        ),
      },
      {
        id: 'vehicleStatus',
        header: t('vehiclesList.columns.status'),
        cell: ({ row }) => <Badge variant="light">{t(`vehiclesList.status.${row.original.vehicleStatus}`)}</Badge>,
      },
    ],
    [t],
  )

  return (
    <Stack gap="md">
      <Group justify="space-between">
        <Title order={2}>{t('vehiclesList.title')}</Title>
        <Button onClick={() => setCreateOpen(true)}>{t('vehiclesList.newVehicle')}</Button>
      </Group>

      <ActionBar
        searchValue={query}
        onSearchChange={setQuery}
        searchPlaceholder={t('vehiclesList.searchPlaceholder')}
        density={density}
        onDensityChange={setDensity}
        onRefresh={() => searchQuery.refetch()}
        refreshing={searchQuery.isRefetching}
        labels={{
          density: {
            compact: t('common.density.compact'),
            default: t('common.density.default'),
            comfortable: t('common.density.comfortable'),
          },
          densityTooltip: (label) => t('common.density.tooltip', { label }),
          densityAriaLabel: t('common.density.ariaLabel'),
          refresh: t('common.refresh'),
        }}
      />

      {selectedIds.size > 0 && (
        <SelectionBar
          count={selectedIds.size}
          onClear={() => setSelectedIds(new Set())}
          countLabel={(n) => t('vehiclesList.selection.count', { count: n })}
          clearLabel={t('vehiclesList.selection.clear')}
          actions={[
            {
              label: t('vehiclesList.selection.copyIds'),
              icon: <Copy size={14} />,
              onClick: () => navigator.clipboard.writeText([...selectedIds].join(', ')),
            },
          ]}
        />
      )}

      {result?.resolved && (
        <Alert icon={<Car size={16} />} color="grape" title={t('vehiclesList.resolved.title')}>
          <Group justify="space-between">
            <div>
              <strong>{result.resolved.vehicleNumber}</strong> — <span style={{ fontFamily: 'monospace' }}>{result.resolved.vin}</span>
            </div>
            <Button
              size="xs"
              variant="light"
              leftSection={<ExternalLink size={14} />}
              onClick={() => navigate(`/vehicles/${result.resolved!.id}`)}
            >
              {t('vehiclesList.resolved.open')}
            </Button>
          </Group>
        </Alert>
      )}

      {result && result.pickerCandidates.length > 0 && (
        <Alert icon={<AlertTriangle size={16} />} color="yellow" title={pickerTitle(t, result)}>
          <Picker
            rows={result.pickerCandidates.map((c) => ({
              id: c.id,
              identifier: c.vehicleNumber,
              label: c.vin,
              sublabel: c.plate ?? undefined,
            }))}
            query=""
            onQueryChange={() => {}}
            onSelect={(row) => navigate(`/vehicles/${row.id}`)}
            autoFocus={false}
            emptyLabel={t('vehiclesList.picker.empty')}
          />
        </Alert>
      )}

      <DataGrid<VehicleMdmRead>
        columns={columns}
        rows={rows}
        getRowId={(row) => row.id}
        sort={sort}
        onSortChange={setSort}
        density={density}
        rowHref={(row) => `/vehicles/${row.id}`}
        selection={{ selectedIds, onSelectionChange: setSelectedIds }}
        loading={searchQuery.isLoading}
        fetchingNextPage={false}
        hasNextPage={false}
        onLoadMore={() => {}}
        error={searchQuery.isError ? t('vehiclesList.loadError') : null}
        onRetry={() => searchQuery.refetch()}
        total={null}
        totalIsEstimate={false}
        isFiltered={debouncedQuery.length > 0}
        labels={{
          showing: (count) => t('common.showing', { count }),
          loadingMore: t('common.loadingMore'),
          retry: t('common.retry'),
          rowActionsLabel: t('common.rowActionsLabel'),
        }}
        emptyState={{
          icon: <Car size={24} />,
          title: t('vehiclesList.emptyState.title'),
          description: t('vehiclesList.emptyState.description'),
          action: <Button onClick={() => setCreateOpen(true)}>{t('vehiclesList.newVehicle')}</Button>,
        }}
        emptyFilteredState={{
          icon: <Car size={24} />,
          title: t('vehiclesList.emptyFilteredState.title'),
          description: t('vehiclesList.emptyFilteredState.description'),
          action: (
            <Button variant="default" onClick={() => setQuery('')}>
              {t('vehiclesList.emptyFilteredState.action')}
            </Button>
          ),
        }}
      />

      <VehicleCreateDialog
        opened={createOpen}
        onClose={() => setCreateOpen(false)}
        onCreated={(vehicle) => {
          setCreateOpen(false)
          navigate(`/vehicles/${vehicle.id}`)
        }}
      />
    </Stack>
  )
}

function NotSet() {
  const { t } = useTranslation()
  return <span style={{ fontStyle: 'italic', color: 'var(--mantine-color-gray-5)' }}>{t('common.notSet')}</span>
}

function pickerTitle(t: (key: string) => string, result: VehicleSearchResult): string {
  const isWechselschild = result.pickerCandidates.every((c) => !c.isConflict)
  return isWechselschild ? t('vehiclesList.picker.wechselschildTitle') : t('vehiclesList.picker.conflictTitle')
}
