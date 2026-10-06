import { useMemo } from 'react'
import { useInfiniteQuery } from '@tanstack/react-query'
import { Building2 } from 'lucide-react'
import { useTranslation } from 'react-i18next'
import { ActionBar, DataGrid, OverviewShellRegion, type SortSpec } from '@nexotec/ui-kit'
import { useUiPreferencesContext } from '../../hooks/UiPreferencesContext'
import { api } from '../../api/client'
import { toSwissLocale, type SupportedLanguage } from '../../i18n'
import { buildStockGroupColumns } from './columns/stockGroupColumns'
import { serializeSort } from '../../utils/sortParam'
import type { StockItemGroupPage } from '../../api/types'

interface GroupStockGridProps {
  /** Search and sort are owned by StockListPage, which keeps them in the
   * URL (§ ADR-056) alongside `?scope=group`. */
  query: string
  debouncedQuery: string
  onQueryChange: (query: string) => void
  sort: SortSpec[]
  onSortChange: (sort: SortSpec[]) => void
}

/**
 * § ADR-055 — "a different artefact, dressed differently, not a greyed-
 * out version of your own grid." Deliberately simpler chrome than the
 * own-stock grid: no saved views, no column config panel, no filters —
 * the group projection doesn't carry the fields those would operate on
 * anyway, and there is no per-user preference worth persisting for a
 * read-only cross-dealership roster.
 *
 * Sort, search and paging are server-side, like the own-stock grid (UI/UX
 * Core Principles; KAN-152) — the group roster can be the whole group's
 * stock, so it is never loaded into one response or filtered here.
 */
export function GroupStockGrid({ query, debouncedQuery, onQueryChange, sort, onSortChange }: GroupStockGridProps) {
  const { t, i18n } = useTranslation()
  const locale = toSwissLocale(i18n.language as SupportedLanguage)
  const { density, setDensity } = useUiPreferencesContext()

  const sortParam = sort.length > 0 ? serializeSort(sort) : undefined

  const groupQuery = useInfiniteQuery({
    queryKey: ['stock-items', 'group', debouncedQuery, sortParam],
    queryFn: async ({ pageParam }: { pageParam: string | null }) => {
      const params = new URLSearchParams()
      if (debouncedQuery) params.set('q', debouncedQuery)
      if (sortParam) params.set('sort', sortParam)
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

  return (
    <OverviewShellRegion
      header={<div />}
      actionBar={
        <ActionBar
          searchValue={query}
          onSearchChange={onQueryChange}
          searchPlaceholder={t('stockList.searchPlaceholder')}
          density={density}
          onDensityChange={setDensity}
          onRefresh={() => groupQuery.refetch()}
          refreshing={groupQuery.isRefetching}
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
        isFiltered={debouncedQuery.length > 0}
        labels={{
          showing: (count) => t('common.showing', { count }),
          showingOfTotal: (count, totalStr) => t('common.showingOfTotal', { count, total: totalStr }),
          loadingMore: t('common.loadingMore'),
          retry: t('common.retry'),
          rowActionsLabel: t('common.rowActionsLabel'),
        }}
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
