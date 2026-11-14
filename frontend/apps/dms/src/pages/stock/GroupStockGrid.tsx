import { useMemo, useState } from 'react'
import { useInfiniteQuery, useQuery } from '@tanstack/react-query'
import { Building2 } from 'lucide-react'
import { useTranslation } from 'react-i18next'
import {
  ActionBar,
  ColumnConfigPanel,
  type ColumnRegistryEntry,
  DataGrid,
  defaultColumnLayout,
  type FilterPredicate,
  offeredPredicates,
  OverviewShellRegion,
  type SavedView,
  type SortSpec,
  sortSpecToQueryParam,
  ViewsAndFilters,
} from '@nexotec/ui-kit'
import { useUiPreferencesContext } from '../../hooks/UiPreferencesContext'
import { useGridPreferences } from '../../hooks/useGridPreferences'
import { useSavedViews } from '../../hooks/useSavedViews'
import { useViewsAndFiltersLabels } from '../../hooks/useViewsAndFiltersLabels'
import { applyGroupStockFilters, buildGroupStockFilterFields } from '../../stockOptions'
import { useColumnPanelLabels } from '../../hooks/useColumnPanelLabels'
import { api } from '../../api/client'
import { toSwissLocale, type SupportedLanguage } from '../../i18n'
import { buildStockGroupColumns } from './columns/stockGroupColumns'
import type { GroupStockDealershipList, StockItemGroupPage } from '../../api/types'
import { useActionBarLabels } from '../../hooks/useActionBarLabels'
import { useDataGridLabels } from '../../hooks/useDataGridLabels'

interface GroupStockGridProps {
  /** Search and sort are owned by StockListPage, which keeps them in the
   * URL (§ ADR-056) alongside `?scope=group`. */
  query: string
  debouncedQuery: string
  onQueryChange: (query: string) => void
  sort: SortSpec[]
  onSortChange: (sort: SortSpec[]) => void
  /** The URL's `filters`, as stored — this grid narrows them to what it
   * offers (ADR-102) before it sends or shows any. */
  filters: FilterPredicate[]
  onFiltersChange: (filters: FilterPredicate[]) => void
}

/** A stable grid key: renaming it discards every user's layout. Column
 * layout is stored "one per user per grid" (UI/UX Specification,
 * User-Level Preference Persistence), and the group roster is its own grid,
 * so it has its own key, never the own-stock grid's. */
const GROUP_GRID_KEY = 'inventory.stock.group'

/**
 * § ADR-055 — "a different artefact, dressed differently, not a greyed-
 * out version of your own grid." Simpler chrome than the own-stock grid:
 * one filter, the Betrieb (KAN-565: ADR-055's «the dealership as a
 * filter» — «ist» / «ist nicht», the options the group's roster from
 * `/inventory/groups/mine/dealerships`), in the one views-and-filters
 * control (ADR-058) with views saved under this grid's own key; its
 * predicates live in the URL beside `?scope=group` (ADR-056). KAN-357: it does have the action bar's
 * Columns control (FR-UI-02, the right cluster "identical in all nine
 * modules"), so every column of the group projection can be put on screen
 * (ADR-060) — its layout persisted per user under its own grid key. Its
 * sort stays in the URL alone (StockListPage).
 *
 * Sort, search and paging are server-side, like the own-stock grid (UI/UX
 * Core Principles; KAN-152) — the group roster can be the whole group's
 * stock, so it is never loaded into one response or filtered here.
 */
export function GroupStockGrid({
  query,
  debouncedQuery,
  onQueryChange,
  sort,
  onSortChange,
  filters,
  onFiltersChange,
}: GroupStockGridProps) {
  const { t, i18n } = useTranslation()
  const dataGridLabels = useDataGridLabels()
  const locale = toSwissLocale(i18n.language as SupportedLanguage)
  const { density, setDensity } = useUiPreferencesContext()
  // Only the column layout is read from here; the sort is the URL's.
  const gridPrefs = useGridPreferences(GROUP_GRID_KEY, { sort: [] })

  const savedViews = useSavedViews(GROUP_GRID_KEY)
  const [appliedViewId, setAppliedViewId] = useState<string | null>(null)

  const sortParam = sortSpecToQueryParam(sort)

  // KAN-565: the Betriebe the filter offers — the listing's own roster.
  const dealershipsQuery = useQuery({
    queryKey: ['stock-items', 'group', 'dealerships'],
    queryFn: () => api.get<GroupStockDealershipList>('/inventory/groups/mine/dealerships'),
  })
  const filterFields = useMemo(
    () => buildGroupStockFilterFields(t, dealershipsQuery.data?.items ?? []),
    [t, dealershipsQuery.data]
  )
  // ADR-102: a predicate from the URL or a saved view this grid does not
  // offer is neither sent nor shown.
  const predicates = useMemo(() => offeredPredicates(filters, filterFields), [filters, filterFields])

  const groupQuery = useInfiniteQuery({
    queryKey: ['stock-items', 'group', debouncedQuery, sortParam, predicates],
    queryFn: async ({ pageParam }: { pageParam: string | null }) => {
      const params = new URLSearchParams()
      if (debouncedQuery) params.set('q', debouncedQuery)
      if (sortParam) params.set('sort', sortParam)
      applyGroupStockFilters(params, predicates)
      params.set('limit', '50')
      if (pageParam) params.set('cursor', pageParam)
      return api.get<StockItemGroupPage>(`/inventory/groups/mine/stock-items?${params.toString()}`)
    },
    initialPageParam: null as string | null,
    getNextPageParam: (lastPage) => lastPage.nextCursor,
  })

  const rows = useMemo(() => groupQuery.data?.pages.flatMap((page) => page.items) ?? [], [groupQuery.data])
  const total = groupQuery.data?.pages[0]?.total ?? null
  const totalIsEstimate = groupQuery.data?.pages[0]?.totalIsEstimate ?? false

  const columns = useMemo(() => buildStockGroupColumns(t, locale), [t, locale])
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
  // A user with no saved layout sees the documented default subset, the
  // rest one click away (ADR-060) — as on Customers. DataGrid without a
  // layout would render every column, defaultVisible ignored.
  const columnLayout = useMemo(
    () => gridPrefs.columnLayout ?? defaultColumnLayout(columnRegistry),
    [gridPrefs.columnLayout, columnRegistry]
  )
  const columnPanelLabels = useColumnPanelLabels()
  const actionBarLabels = useActionBarLabels()
  const viewsAndFiltersLabels = useViewsAndFiltersLabels()
  const currentViewName =
    (appliedViewId && savedViews.views.find((v) => v.id === appliedViewId)?.name) || t('stockList.allGroupStockView')

  const applyView = (view: SavedView) => {
    setAppliedViewId(view.id)
    if (view.snapshot.sort) onSortChange(view.snapshot.sort)
    if (view.snapshot.columnLayout) gridPrefs.setColumnLayout(view.snapshot.columnLayout)
    onFiltersChange(view.snapshot.filters ?? [])
  }

  return (
    <OverviewShellRegion
      header={<div />}
      actionBar={
        <ActionBar
          labels={actionBarLabels}
          searchValue={query}
          onSearchChange={onQueryChange}
          searchPlaceholder={t('stockList.searchPlaceholder')}
          density={density}
          onDensityChange={setDensity}
          onRefresh={() => groupQuery.refetch()}
          refreshing={groupQuery.isRefetching}
          filterSlot={
            <ViewsAndFilters
              labels={viewsAndFiltersLabels}
              currentViewName={currentViewName}
              views={savedViews.views}
              onApplyView={applyView}
              onSaveCurrentAsView={(name) =>
                savedViews.saveView(name, { sort, columnLayout: gridPrefs.columnLayout ?? undefined, filters: predicates })
              }
              onDeleteView={savedViews.deleteView}
              onSetDefaultView={savedViews.setDefaultView}
              fields={filterFields}
              narrowPredicates={(stored) => offeredPredicates(stored, filterFields)}
              predicates={predicates}
              onPredicatesChange={(next) => {
                onFiltersChange(next)
                setAppliedViewId(null)
              }}
              onResetFilters={() => {
                onFiltersChange([])
                setAppliedViewId(null)
              }}
            />
          }
          columnsSlot={
            <ColumnConfigPanel
              registry={columnRegistry}
              layout={columnLayout}
              onLayoutChange={gridPrefs.setColumnLayout}
              labels={columnPanelLabels}
            />
          }
        />
      }
    >
      <DataGrid
        columns={columns}
        rows={rows}
        getRowId={(row) => row.id}
        sort={sort}
        onSortChange={onSortChange}
        density={density}
        loading={groupQuery.isLoading}
        refetching={groupQuery.isRefetching && !groupQuery.isLoading}
        fetchingNextPage={groupQuery.isFetchingNextPage}
        hasNextPage={Boolean(groupQuery.hasNextPage)}
        onLoadMore={() => groupQuery.fetchNextPage()}
        error={groupQuery.isError ? t('stockList.groupLoadError') : null}
        onRetry={() => groupQuery.refetch()}
        total={total}
        totalIsEstimate={totalIsEstimate}
        isFiltered={debouncedQuery.length > 0 || predicates.length > 0}
        columnLayout={columnLayout}
        onColumnLayoutChange={gridPrefs.setColumnLayout}
        labels={dataGridLabels}
        emptyState={{
          icon: <Building2 size={24} />,
          title: t('stockList.emptyState.title'),
          description: t('stockList.emptyState.description'),
        }}
        emptyFilteredState={{
          icon: <Building2 size={24} />,
          title: t('stockList.emptyFilteredState.title'),
          description: t('stockList.emptyFilteredState.description'),
        }}
      />
    </OverviewShellRegion>
  )
}
