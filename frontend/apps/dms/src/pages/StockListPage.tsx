import { useEffect, useMemo, useState } from 'react'
import { Link, useNavigate, useSearchParams } from 'react-router-dom'
import { Button, Group, Stack, Title } from '@mantine/core'
import { useDebouncedValue } from '@mantine/hooks'
import { useInfiniteQuery } from '@tanstack/react-query'
import { ExternalLink, Warehouse } from 'lucide-react'
import { useTranslation } from 'react-i18next'
import {
  ActionBar,
  ColumnConfigPanel,
  DataGrid,
  OverviewShellRegion,
  StockConditionBadge,
  StockLifecycleBadge,
  StockReservationBadge,
  ViewsAndFilters,
  semantic,
  useSetBreadcrumb,
  type ColumnRegistryEntry,
  type FilterFieldDef,
  type FilterPredicate,
  type GridColumnDef,
  type SavedView,
  type SortSpec,
} from '@nexotec/ui-kit'
import { useUiPreferencesContext } from '../hooks/UiPreferencesContext'
import { useGridPreferences } from '../hooks/useGridPreferences'
import { useSavedViews } from '../hooks/useSavedViews'
import { api } from '../api/client'
import { toSwissLocale, type SupportedLanguage } from '../i18n'
import {
  translatedStockConditionLabel,
  translatedStockConditionOptions,
  translatedStockLifecycleLabel,
  translatedStockLifecycleOptions,
  translatedStockReservationLabel,
} from '../stockOptions'
import { formatDate, formatCurrencyChf } from '../utils/format'
import { GroupStockGrid } from './stock/GroupStockGrid'
import { ScopeSwitchMenu, type StockScope } from './stock/components/ScopeSwitchMenu'
import type { StockItemPage, StockItemRead } from '../api/types'

const GRID_KEY = 'inventory.stock.list'
const DEFAULT_SORT: SortSpec[] = [{ field: 'updatedAt', direction: 'desc' }]

function parseSortParam(raw: string): SortSpec[] {
  return raw
    .split(',')
    .map((part): SortSpec | null => {
      const [field, direction] = part.split(':')
      if (!field) return null
      return { field, direction: direction === 'asc' ? 'asc' : 'desc' }
    })
    .filter((s): s is SortSpec => s !== null)
}

function serializeSort(sort: SortSpec[]): string {
  return sort.map((s) => `${s.field}:${s.direction}`).join(',')
}

function buildFilterFields(t: (key: string) => string): FilterFieldDef[] {
  return [
    {
      id: 'lifecycleStatus',
      label: t('stockList.columns.lifecycleStatus'),
      type: 'select',
      options: translatedStockLifecycleOptions(t),
    },
    {
      id: 'condition',
      label: t('stockList.columns.condition'),
      type: 'select',
      options: translatedStockConditionOptions(t),
    },
  ]
}

/** `/inventory/stock-items` accepts one equality filter today
 * (lifecycle_status) — condition has no server-side filter param yet, so
 * that predicate is left unsent rather than silently misfiltering (same
 * honest-gap pattern CustomersListPage's own applyPredicatesToParams
 * documents).
 */
function applyPredicatesToParams(params: URLSearchParams, predicates: FilterPredicate[]) {
  for (const predicate of predicates) {
    if (predicate.fieldId === 'lifecycleStatus' && predicate.condition === 'is' && typeof predicate.value === 'string') {
      params.set('lifecycle_status', predicate.value)
    }
  }
}

export function StockListPage() {
  const { t, i18n } = useTranslation()
  const locale = toSwissLocale(i18n.language as SupportedLanguage)
  useSetBreadcrumb([t('shell.nav.inventory')])
  const navigate = useNavigate()
  const { density, setDensity } = useUiPreferencesContext()
  const gridPrefs = useGridPreferences(GRID_KEY, { sort: DEFAULT_SORT })
  const savedViews = useSavedViews(GRID_KEY)
  const [searchParams, setSearchParams] = useSearchParams()
  // § ADR-056 — layout/scope choices like this belong on the reader's own
  // ergonomics record, not the shareable URL (only search/sort/filters are
  // URL-synced on this screen); local state is the right home for it.
  const [scope, setScope] = useState<StockScope>('own')

  const sort = searchParams.get('sort') ? parseSortParam(searchParams.get('sort')!) : gridPrefs.sort
  const predicates = searchParams.get('filters') ? safeParseFilters(searchParams.get('filters')!) : []

  const updateUrl = (patch: Record<string, string | null>) => {
    setSearchParams(
      (prev) => {
        const next = new URLSearchParams(prev)
        for (const [key, value] of Object.entries(patch)) {
          if (value === null) next.delete(key)
          else next.set(key, value)
        }
        return next
      },
      { replace: true }
    )
  }

  const setSort = (next: SortSpec[]) => {
    gridPrefs.setSort(next)
    updateUrl({ sort: next.length > 0 ? serializeSort(next) : null })
  }

  const setPredicates = (next: FilterPredicate[]) => {
    updateUrl({ filters: next.length > 0 ? JSON.stringify(next) : null })
  }

  const [query, setQuery] = useState(() => searchParams.get('q') ?? '')
  const [debouncedQuery] = useDebouncedValue(query, 250)
  useEffect(() => {
    updateUrl({ q: debouncedQuery || null })
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [debouncedQuery])

  const [appliedViewId, setAppliedViewId] = useState<string | null>(null)

  const sortParam = sort.length > 0 ? sort.map((s) => `${s.field}:${s.direction}`).join(',') : undefined

  const { data, fetchNextPage, hasNextPage, isFetchingNextPage, isLoading, isError, refetch, isRefetching } =
    useInfiniteQuery({
      queryKey: ['stock-items', GRID_KEY, debouncedQuery, sortParam, predicates],
      queryFn: async ({ pageParam }: { pageParam: string | null }) => {
        const params = new URLSearchParams()
        if (debouncedQuery) params.set('q', debouncedQuery)
        if (sortParam) params.set('sort', sortParam)
        applyPredicatesToParams(params, predicates)
        params.set('limit', '50')
        if (pageParam) params.set('cursor', pageParam)
        return api.get<StockItemPage>(`/inventory/stock-items?${params.toString()}`)
      },
      initialPageParam: null as string | null,
      getNextPageParam: (lastPage) => lastPage.nextCursor,
    })

  const rows = useMemo(() => data?.pages.flatMap((page) => page.items) ?? [], [data])
  const total = data?.pages[0]?.total ?? null
  const totalIsEstimate = data?.pages[0]?.totalIsEstimate ?? false

  const filterFields = useMemo(() => buildFilterFields(t), [t])

  // § ADR-060 — the full ~60-column registry lands in PR-9; these are the
  // grid's own confirmed default-visible set from the reference prototype.
  const columns: GridColumnDef<StockItemRead>[] = useMemo(
    () => [
      {
        id: 'stockNumber',
        header: t('stockList.columns.stockNumber'),
        cell: ({ row }) => row.original.stockNumber,
        meta: { sortField: 'stockNumber', pinned: 'left', mono: true, locked: true },
      },
      {
        id: 'vehicleLabel',
        header: t('stockList.columns.vehicle'),
        cell: ({ row }) => <span style={{ fontWeight: 600 }}>{row.original.vehicleLabel}</span>,
        meta: { locked: true, secondary: (row) => row.vin },
      },
      {
        id: 'condition',
        header: t('stockList.columns.condition'),
        cell: ({ row }) => (
          <StockConditionBadge condition={row.original.condition} label={translatedStockConditionLabel(t, row.original.condition)} />
        ),
      },
      {
        id: 'lifecycleStatus',
        header: t('stockList.columns.lifecycleStatus'),
        cell: ({ row }) => (
          <StockLifecycleBadge status={row.original.lifecycleStatus} label={translatedStockLifecycleLabel(t, row.original.lifecycleStatus)} />
        ),
      },
      {
        id: 'reservationState',
        header: t('stockList.columns.reservationState'),
        cell: ({ row }) => (
          <StockReservationBadge state={row.original.reservationState} label={translatedStockReservationLabel(t, row.original.reservationState)} />
        ),
      },
      {
        id: 'odometerKm',
        header: t('stockList.columns.odometerKm'),
        cell: ({ row }) => (row.original.odometerKm != null ? row.original.odometerKm.toLocaleString(locale) : '—'),
        meta: { align: 'right' },
      },
      {
        id: 'effectivePrice',
        header: t('stockList.columns.effectivePrice'),
        cell: ({ row }) => (row.original.effectivePrice != null ? formatCurrencyChf(Number(row.original.effectivePrice)) : '—'),
        meta: { align: 'right' },
      },
      {
        id: 'updatedAt',
        header: t('stockList.columns.changed'),
        cell: ({ row }) => formatDate(row.original.updatedAt, locale),
        meta: { sortField: 'updatedAt', align: 'right' },
      },
      // ADR-060: every persisted field is a column — VIN hidden by default
      // (already ride-alongs as vehicleLabel's secondary line above).
      {
        id: 'vin',
        header: t('stockList.columns.vin'),
        cell: ({ row }) => row.original.vin ?? '—',
        meta: { defaultVisible: false, mono: true, sortField: 'vin' },
      },
      {
        id: 'listPrice',
        header: t('stockList.columns.listPrice'),
        cell: ({ row }) => (row.original.listPrice != null ? formatCurrencyChf(Number(row.original.listPrice)) : '—'),
        meta: { defaultVisible: false, align: 'right' },
      },
      // § ADR-060 — every persisted field is a column; a documented
      // subset (the ones above) is visible by default. These remaining
      // fields round out the registry toward its full target without
      // claiming an exact count.
      {
        id: 'ageingBucket',
        header: t('stockList.columns.ageingBucket'),
        cell: ({ row }) =>
          row.original.ageingBucket ? (
            <span
              aria-label={row.original.ageingBucket}
              style={{
                display: 'inline-block',
                width: 10,
                height: 10,
                borderRadius: '50%',
                backgroundColor:
                  row.original.ageingBucket === 'green'
                    ? semantic.success.text
                    : row.original.ageingBucket === 'amber'
                      ? semantic.warning.text
                      : semantic.destructive.text,
              }}
            />
          ) : (
            '—'
          ),
        meta: { defaultVisible: false },
      },
      {
        id: 'basePrice',
        header: t('stockList.columns.basePrice'),
        cell: ({ row }) => (row.original.basePrice != null ? formatCurrencyChf(Number(row.original.basePrice)) : '—'),
        meta: { defaultVisible: false, align: 'right' },
      },
      {
        id: 'purchasePrice',
        header: t('stockList.columns.purchasePrice'),
        cell: ({ row }) => (row.original.purchasePrice != null ? formatCurrencyChf(Number(row.original.purchasePrice)) : '—'),
        meta: { defaultVisible: false, align: 'right' },
      },
      {
        id: 'landedCost',
        header: t('stockList.columns.landedCost'),
        cell: ({ row }) => (row.original.landedCost != null ? formatCurrencyChf(Number(row.original.landedCost)) : '—'),
        meta: { defaultVisible: false, align: 'right' },
      },
      {
        id: 'notionalInputTaxAmount',
        header: t('stockList.columns.notionalInputTaxAmount'),
        cell: ({ row }) => (row.original.notionalInputTaxAmount != null ? formatCurrencyChf(Number(row.original.notionalInputTaxAmount)) : '—'),
        meta: { defaultVisible: false, align: 'right' },
      },
      {
        id: 'isInvoiceable',
        header: t('stockList.columns.isInvoiceable'),
        cell: ({ row }) => (row.original.isInvoiceable ? t('common.yes') : t('common.no')),
        meta: { defaultVisible: false },
      },
      {
        id: 'supplierName',
        header: t('stockList.columns.supplierName'),
        cell: ({ row }) => row.original.supplierName ?? '—',
        meta: { defaultVisible: false },
      },
      {
        id: 'purchaseDate',
        header: t('stockList.columns.purchaseDate'),
        cell: ({ row }) => (row.original.purchaseDate ? formatDate(row.original.purchaseDate, locale) : '—'),
        meta: { defaultVisible: false },
      },
      {
        id: 'orderDate',
        header: t('stockList.columns.orderDate'),
        cell: ({ row }) => (row.original.orderDate ? formatDate(row.original.orderDate, locale) : '—'),
        meta: { defaultVisible: false },
      },
      {
        id: 'expectedDelivery',
        header: t('stockList.columns.expectedDelivery'),
        cell: ({ row }) => (row.original.expectedDelivery ? formatDate(row.original.expectedDelivery, locale) : '—'),
        meta: { defaultVisible: false },
      },
      {
        id: 'pipelineRef',
        header: t('stockList.columns.pipelineRef'),
        cell: ({ row }) => row.original.pipelineRef ?? '—',
        meta: { defaultVisible: false, mono: true },
      },
    ],
    [t, locale]
  )

  const columnRegistry: ColumnRegistryEntry[] = useMemo(
    () =>
      columns.map((c) => ({
        id: String(c.id),
        label: typeof c.header === 'string' ? c.header : String(c.id),
        defaultVisible: c.meta?.defaultVisible ?? true,
        locked: c.meta?.locked,
      })),
    [columns]
  )

  const isFiltered = debouncedQuery.length > 0 || predicates.length > 0
  const currentViewName = (appliedViewId && savedViews.views.find((v) => v.id === appliedViewId)?.name) || t('stockList.allStockView')

  const applyView = (view: SavedView) => {
    setAppliedViewId(view.id)
    if (view.snapshot.sort) setSort(view.snapshot.sort)
    if (view.snapshot.columnLayout) gridPrefs.setColumnLayout(view.snapshot.columnLayout)
    if (view.snapshot.filters) setPredicates(view.snapshot.filters)
  }

  return (
    <Stack gap="md">
      <Group justify="space-between">
        <Title order={2}>{t('stockList.title')}</Title>
        <Group gap="sm">
          <ScopeSwitchMenu scope={scope} onScopeChange={setScope} />
          <Button component={Link} to="/stock/new">
            {t('stockList.newStockItem')}
          </Button>
        </Group>
      </Group>

      {scope === 'group' ? (
        <GroupStockGrid />
      ) : (
      <OverviewShellRegion
        header={<div />}
        actionBar={
          <ActionBar
            searchValue={query}
            onSearchChange={setQuery}
            searchPlaceholder={t('stockList.searchPlaceholder')}
            density={density}
            onDensityChange={setDensity}
            onRefresh={() => refetch()}
            refreshing={isRefetching}
            filterSlot={
              <ViewsAndFilters
                currentViewName={currentViewName}
                views={savedViews.views}
                onApplyView={applyView}
                onSaveCurrentAsView={(name) =>
                  savedViews.saveView(name, { sort, columnLayout: gridPrefs.columnLayout ?? undefined, filters: predicates })
                }
                onDeleteView={savedViews.deleteView}
                onSetDefaultView={savedViews.setDefaultView}
                fields={filterFields}
                predicates={predicates}
                onPredicatesChange={(next) => {
                  setPredicates(next)
                  setAppliedViewId(null)
                }}
                onResetFilters={() => {
                  setPredicates([])
                  setAppliedViewId(null)
                }}
              />
            }
            columnsSlot={
              <ColumnConfigPanel
                registry={columnRegistry}
                layout={
                  gridPrefs.columnLayout ?? {
                    order: columnRegistry.map((c) => c.id),
                    hidden: columnRegistry.filter((c) => !c.defaultVisible).map((c) => c.id),
                    widths: {},
                    pinnedLeft: [],
                  }
                }
                onLayoutChange={gridPrefs.setColumnLayout}
              />
            }
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
        }
      >
        <DataGrid<StockItemRead>
          columns={columns}
          rows={rows}
          getRowId={(row) => row.id}
          sort={sort}
          onSortChange={setSort}
          density={density}
          rowHref={(row) => `/stock/${row.id}`}
          linkComponent={Link}
          loading={isLoading}
          refetching={isRefetching && !isLoading}
          fetchingNextPage={isFetchingNextPage}
          hasNextPage={Boolean(hasNextPage)}
          onLoadMore={() => fetchNextPage()}
          error={isError ? 'Failed to load stock.' : null}
          onRetry={() => refetch()}
          total={total}
          totalIsEstimate={totalIsEstimate}
          isFiltered={isFiltered}
          locale={locale}
          columnLayout={gridPrefs.columnLayout ?? undefined}
          onColumnLayoutChange={gridPrefs.setColumnLayout}
          labels={{
            showing: (count) => t('common.showing', { count }),
            showingOfTotal: (count, totalStr) => t('common.showingOfTotal', { count, total: totalStr }),
            loadingMore: t('common.loadingMore'),
            retry: t('common.retry'),
            rowActionsLabel: t('common.rowActionsLabel'),
          }}
          emptyState={{
            icon: <Warehouse size={24} />,
            title: t('stockList.emptyState.title'),
            description: t('stockList.emptyState.description'),
            action: (
              <Button component={Link} to="/stock/new">
                {t('stockList.newStockItem')}
              </Button>
            ),
          }}
          emptyFilteredState={{
            icon: <Warehouse size={24} />,
            title: t('stockList.emptyFilteredState.title'),
            description: t('stockList.emptyFilteredState.description'),
            action: (
              <Button
                variant="default"
                onClick={() => {
                  setQuery('')
                  setPredicates([])
                  setAppliedViewId(null)
                }}
              >
                {t('stockList.emptyFilteredState.action')}
              </Button>
            ),
          }}
          rowActions={(row) => ({
            navigate: [
              {
                label: t('stockList.rowActions.open'),
                icon: <ExternalLink size={16} />,
                onClick: () => navigate(`/stock/${row.id}`),
              },
            ],
          })}
        />
      </OverviewShellRegion>
      )}
    </Stack>
  )
}

function safeParseFilters(raw: string): FilterPredicate[] {
  try {
    const parsed: unknown = JSON.parse(raw)
    return Array.isArray(parsed) ? (parsed as FilterPredicate[]) : []
  } catch {
    return []
  }
}
