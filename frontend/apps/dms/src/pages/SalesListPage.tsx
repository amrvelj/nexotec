import { useEffect, useMemo, useState } from 'react'
import { Link, useNavigate, useSearchParams } from 'react-router-dom'
import { Button, Group, Stack, Title } from '@mantine/core'
import { useDebouncedValue } from '@mantine/hooks'
import { useInfiniteQuery } from '@tanstack/react-query'
import { ExternalLink, Handshake } from 'lucide-react'
import { useTranslation } from 'react-i18next'
import {
  ActionBar,
  ColumnConfigPanel,
  DataGrid,
  OverviewShellRegion,
  SalesStatusBadge,
  SalesTypeBadge,
  ViewsAndFilters,
  useSetBreadcrumb,
  type ColumnRegistryEntry,
  type GridColumnDef,
  type SortSpec,
} from '@nexotec/ui-kit'
import { useUiPreferencesContext } from '../hooks/UiPreferencesContext'
import { useGridPreferences } from '../hooks/useGridPreferences'
import { useSavedViews } from '../hooks/useSavedViews'
import { api } from '../api/client'
import { toSwissLocale, type SupportedLanguage } from '../i18n'
import { translatedSalesDealStatusLabel } from '../salesOptions'
import { formatDate, formatCurrencyChf } from '../utils/format'
import type { SalesDealPage, SalesDealRead } from '../api/types'

const GRID_KEY = 'sales.deals.list'
const DEFAULT_SORT: SortSpec[] = [{ field: 'updatedAt', direction: 'desc' }]

type DealTypeFilter = 'all' | 'offer' | 'contract'

function serializeSort(sort: SortSpec[]): string {
  return sort.map((s) => `${s.field}:${s.direction}`).join(',')
}

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

export function SalesListPage() {
  const { t, i18n } = useTranslation()
  const locale = toSwissLocale(i18n.language as SupportedLanguage)
  useSetBreadcrumb([t('shell.nav.sales')])
  const navigate = useNavigate()
  const { density, setDensity } = useUiPreferencesContext()
  const gridPrefs = useGridPreferences(GRID_KEY, { sort: DEFAULT_SORT })
  const savedViews = useSavedViews(GRID_KEY)
  const [searchParams, setSearchParams] = useSearchParams()

  const sort = searchParams.get('sort') ? parseSortParam(searchParams.get('sort')!) : gridPrefs.sort
  // § ADR-056 — search/sort/type-filter are the shareable-URL slice; the
  // reference prototype's own "Alle/Offerten/Verträge" chips are a single
  // equality predicate, not a general filter expression, so they live
  // directly in the URL rather than through ViewsAndFilters' predicate list.
  const dealType = (searchParams.get('type') as DealTypeFilter | null) ?? 'all'

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
      queryKey: ['sales-deals', GRID_KEY, debouncedQuery, sortParam, dealType],
      queryFn: async ({ pageParam }: { pageParam: string | null }) => {
        const params = new URLSearchParams()
        if (debouncedQuery) params.set('q', debouncedQuery)
        if (sortParam) params.set('sort', sortParam)
        if (dealType !== 'all') params.set('entityType', dealType)
        params.set('limit', '50')
        if (pageParam) params.set('cursor', pageParam)
        return api.get<SalesDealPage>(`/sales/deals?${params.toString()}`)
      },
      initialPageParam: null as string | null,
      getNextPageParam: (lastPage) => lastPage.nextCursor,
    })

  const rows = useMemo(() => data?.pages.flatMap((page) => page.items) ?? [], [data])
  const total = data?.pages[0]?.total ?? null
  const totalIsEstimate = data?.pages[0]?.totalIsEstimate ?? false

  // Unfiltered totals for the chip counters — the reference prototype's
  // own "Alle 34 / Offerten 19 / Verträge 15" always reflects the full set,
  // never the current search/sort.
  const { data: allData } = useInfiniteQuery({
    queryKey: ['sales-deals-counts', GRID_KEY],
    queryFn: async () => api.get<SalesDealPage>('/sales/deals?limit=1'),
    initialPageParam: null as string | null,
    getNextPageParam: () => undefined,
  })
  const totalAll = allData?.pages[0]?.total ?? null

  const columns: GridColumnDef<SalesDealRead>[] = useMemo(
    () => [
      {
        id: 'number',
        header: t('salesList.columns.number'),
        cell: ({ row }) => row.original.number,
        meta: { sortField: 'number', pinned: 'left', mono: true, locked: true },
      },
      {
        id: 'entityType',
        header: t('salesList.columns.type'),
        cell: ({ row }) => <SalesTypeBadge entityType={row.original.entityType} />,
        meta: { locked: true },
      },
      {
        id: 'customerLabel',
        header: t('salesList.columns.customer'),
        cell: ({ row }) => row.original.customerLabel ?? '—',
      },
      {
        id: 'vehicleLabel',
        header: t('salesList.columns.vehicle'),
        cell: ({ row }) => row.original.vehicleLabel ?? '—',
      },
      {
        id: 'grossPrice',
        header: t('salesList.columns.grossPrice'),
        cell: ({ row }) =>
          row.original.grossPrice != null ? formatCurrencyChf(Number(row.original.grossPrice)) : '—',
        meta: { sortField: 'grossPrice', align: 'right' },
      },
      {
        id: 'status',
        header: t('salesList.columns.status'),
        cell: ({ row }) => (
          <SalesStatusBadge
            status={row.original.status as never}
            label={translatedSalesDealStatusLabel(t, row.original.status as never)}
          />
        ),
      },
      {
        id: 'margin',
        header: t('salesList.columns.margin'),
        cell: ({ row }) => (row.original.margin != null ? formatCurrencyChf(Number(row.original.margin)) : '—'),
        meta: { align: 'right' },
      },
      {
        id: 'updatedAt',
        header: t('salesList.columns.changed'),
        cell: ({ row }) => formatDate(row.original.updatedAt, locale),
        meta: { sortField: 'updatedAt', align: 'right' },
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

  const isFiltered = debouncedQuery.length > 0
  const currentViewName = (appliedViewId && savedViews.views.find((v) => v.id === appliedViewId)?.name) || t('salesList.allDealsView')

  const rowHref = (row: SalesDealRead) =>
    row.entityType === 'contract' ? `/sales/contracts/${row.contractId}` : `/sales/offers/${row.offerId ?? row.id}`

  return (
    <Stack gap="md">
      <Group justify="space-between">
        <Title order={2}>{t('salesList.title')}</Title>
        <Button component={Link} to="/sales/offers/new">
          {t('salesList.newOffer')}
        </Button>
      </Group>

      <Group gap="xs">
        <Button variant={dealType === 'all' ? 'filled' : 'default'} size="xs" onClick={() => updateUrl({ type: null })}>
          {t('salesList.chips.all')} {totalAll ?? ''}
        </Button>
        <Button
          variant={dealType === 'offer' ? 'filled' : 'default'}
          size="xs"
          onClick={() => updateUrl({ type: 'offer' })}
        >
          {t('salesList.chips.offers')}
        </Button>
        <Button
          variant={dealType === 'contract' ? 'filled' : 'default'}
          size="xs"
          onClick={() => updateUrl({ type: 'contract' })}
        >
          {t('salesList.chips.contracts')}
        </Button>
      </Group>

      <OverviewShellRegion
        header={<div />}
        actionBar={
          <ActionBar
            searchValue={query}
            onSearchChange={setQuery}
            searchPlaceholder={t('salesList.searchPlaceholder')}
            density={density}
            onDensityChange={setDensity}
            onRefresh={() => refetch()}
            refreshing={isRefetching}
            filterSlot={
              <ViewsAndFilters
                currentViewName={currentViewName}
                views={savedViews.views}
                onApplyView={(view) => {
                  setAppliedViewId(view.id)
                  if (view.snapshot.sort) setSort(view.snapshot.sort)
                  if (view.snapshot.columnLayout) gridPrefs.setColumnLayout(view.snapshot.columnLayout)
                }}
                onSaveCurrentAsView={(name) =>
                  savedViews.saveView(name, { sort, columnLayout: gridPrefs.columnLayout ?? undefined, filters: [] })
                }
                onDeleteView={savedViews.deleteView}
                onSetDefaultView={savedViews.setDefaultView}
                fields={[]}
                predicates={[]}
                onPredicatesChange={() => {}}
                onResetFilters={() => {}}
              />
            }
            columnsSlot={
              <ColumnConfigPanel
                registry={columnRegistry}
                layout={
                  gridPrefs.columnLayout ?? {
                    order: columnRegistry.map((c) => c.id),
                    hidden: [],
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
        <DataGrid<SalesDealRead>
          columns={columns}
          rows={rows}
          getRowId={(row) => row.id}
          sort={sort}
          onSortChange={setSort}
          density={density}
          rowHref={rowHref}
          linkComponent={Link}
          loading={isLoading}
          refetching={isRefetching && !isLoading}
          fetchingNextPage={isFetchingNextPage}
          hasNextPage={Boolean(hasNextPage)}
          onLoadMore={() => fetchNextPage()}
          error={isError ? 'Failed to load deals.' : null}
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
            icon: <Handshake size={24} />,
            title: t('salesList.emptyState.title'),
            description: t('salesList.emptyState.description'),
            action: (
              <Button component={Link} to="/sales/offers/new">
                {t('salesList.newOffer')}
              </Button>
            ),
          }}
          emptyFilteredState={{
            icon: <Handshake size={24} />,
            title: t('salesList.emptyFilteredState.title'),
            description: t('salesList.emptyFilteredState.description'),
            action: (
              <Button variant="default" onClick={() => setQuery('')}>
                {t('salesList.emptyFilteredState.action')}
              </Button>
            ),
          }}
          rowActions={(row) => ({
            navigate: [
              {
                label: t('salesList.rowActions.open'),
                icon: <ExternalLink size={16} />,
                onClick: () => navigate(rowHref(row)),
              },
            ],
          })}
        />
      </OverviewShellRegion>
    </Stack>
  )
}
