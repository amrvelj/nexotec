import { useEffect, useMemo, useState } from 'react'
import { Link, useNavigate, useSearchParams } from 'react-router-dom'
import { Button, Group, Stack, Title } from '@mantine/core'
import { useDebouncedValue } from '@mantine/hooks'
import { useInfiniteQuery, useQueryClient } from '@tanstack/react-query'
import { CarFront, ExternalLink } from 'lucide-react'
import { useTranslation } from 'react-i18next'
import {
  ActionBar,
  ColumnConfigPanel,
  DataGrid,
  OverviewShellRegion,
  ValuationSourceBadge,
  ValuationStatusBadge,
  ViewsAndFilters,
  useSetBreadcrumb,
  type ColumnRegistryEntry,
  type GridColumnDef,
  type SortSpec,
} from '@nexotec/ui-kit'
import { api } from '../api/client'
import { buildValuationRowMenu } from '../components/valuationRowMenu'
import { useGridPreferences } from '../hooks/useGridPreferences'
import { useSavedViews } from '../hooks/useSavedViews'
import { useUiPreferencesContext } from '../hooks/UiPreferencesContext'
import { toSwissLocale, type SupportedLanguage } from '../i18n'
import { formatCurrencyChf, formatDate } from '../utils/format'
import type { ValuationPage, ValuationRead } from '../api/types'

const GRID_KEY = 'valuations.list'
const DEFAULT_SORT: SortSpec[] = [{ field: 'createdAt', direction: 'desc' }]

// Confirmed live: Alle / Gültig / Läuft ab / Abgelaufen / Ohne Kunde / Meine.
type Chip = 'all' | 'valid' | 'expiring_soon' | 'expired' | 'unattached' | 'mine'

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

function vehicleLabel(row: ValuationRead): string {
  return [row.vehicleMake, row.vehicleModel, row.vehicleTrim].filter(Boolean).join(' ') || row.vehicleVin || '—'
}

export function ValuationsListPage() {
  const { t, i18n } = useTranslation()
  const locale = toSwissLocale(i18n.language as SupportedLanguage)
  useSetBreadcrumb([t('shell.nav.valuations')])
  const navigate = useNavigate()
  const queryClient = useQueryClient()
  const { density, setDensity } = useUiPreferencesContext()
  const gridPrefs = useGridPreferences(GRID_KEY, { sort: DEFAULT_SORT })
  const savedViews = useSavedViews(GRID_KEY)
  const [searchParams, setSearchParams] = useSearchParams()

  const sort = searchParams.get('sort') ? parseSortParam(searchParams.get('sort')!) : gridPrefs.sort
  const chip = (searchParams.get('chip') as Chip | null) ?? 'all'

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
      queryKey: ['valuations', GRID_KEY, debouncedQuery, sortParam, chip],
      queryFn: async ({ pageParam }: { pageParam: string | null }) => {
        const params = new URLSearchParams()
        if (debouncedQuery) params.set('q', debouncedQuery)
        if (sortParam) params.set('sort', sortParam)
        if (chip !== 'all') params.set('chip', chip)
        params.set('limit', '50')
        if (pageParam) params.set('cursor', pageParam)
        return api.get<ValuationPage>(`/valuations?${params.toString()}`)
      },
      initialPageParam: null as string | null,
      getNextPageParam: (lastPage) => lastPage.nextCursor,
    })

  const rows = useMemo(() => data?.pages.flatMap((page) => page.items) ?? [], [data])
  const total = data?.pages[0]?.total ?? null
  const totalIsEstimate = data?.pages[0]?.totalIsEstimate ?? false

  const columns: GridColumnDef<ValuationRead>[] = useMemo(
    () => [
      {
        id: 'valuationNumber',
        header: t('valuationsList.columns.number'),
        cell: ({ row }) => row.original.valuationNumber,
        meta: { sortField: 'valuationNumber', pinned: 'left', mono: true, locked: true },
      },
      {
        id: 'vehicle',
        header: t('valuationsList.columns.vehicle'),
        cell: ({ row }) => vehicleLabel(row.original),
      },
      {
        id: 'customerLabel',
        header: t('valuationsList.columns.customer'),
        cell: ({ row }) => row.original.customerLabel ?? t('valuationsList.noCustomer'),
      },
      {
        id: 'mileage',
        header: t('valuationsList.columns.mileage'),
        cell: ({ row }) => (row.original.mileage != null ? row.original.mileage.toLocaleString(locale) : '—'),
        meta: { sortField: 'mileage', align: 'right' },
      },
      {
        id: 'finalOffer',
        header: t('valuationsList.columns.finalOffer'),
        cell: ({ row }) => formatCurrencyChf(Number(row.original.finalOffer)),
        meta: { sortField: 'finalOffer', align: 'right' },
      },
      {
        id: 'source',
        header: t('valuationsList.columns.source'),
        cell: ({ row }) => <ValuationSourceBadge source={row.original.source} />,
      },
      {
        id: 'status',
        header: t('valuationsList.columns.status'),
        cell: ({ row }) => <ValuationStatusBadge status={row.original.status} />,
      },
      {
        id: 'validUntil',
        header: t('valuationsList.columns.validUntil'),
        cell: ({ row }) => formatDate(row.original.validUntil, locale),
        meta: { sortField: 'validUntil', align: 'right' },
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
  const currentViewName =
    (appliedViewId && savedViews.views.find((v) => v.id === appliedViewId)?.name) || t('valuationsList.allView')

  const rowHref = (row: ValuationRead) => `/valuations/${row.id}`

  const markUsed = async (valuation: ValuationRead) => {
    await api.post(`/valuations/${valuation.id}/mark-used`, undefined, { 'If-Match': String(valuation.version) })
    await queryClient.invalidateQueries({ queryKey: ['valuations'] })
  }

  return (
    <Stack gap="md">
      <Group justify="space-between">
        <Title order={2}>{t('valuationsList.title')}</Title>
        <Button component={Link} to="/valuations/new">
          {t('valuationsList.newValuation')}
        </Button>
      </Group>

      <Group gap="xs">
        {(['all', 'valid', 'expiring_soon', 'expired', 'unattached', 'mine'] as Chip[]).map((c) => (
          <Button
            key={c}
            variant={chip === c ? 'filled' : 'default'}
            size="xs"
            onClick={() => updateUrl({ chip: c === 'all' ? null : c })}
          >
            {t(`valuationsList.chips.${c}`)}
          </Button>
        ))}
      </Group>

      <OverviewShellRegion
        header={<div />}
        actionBar={
          <ActionBar
            searchValue={query}
            onSearchChange={setQuery}
            searchPlaceholder={t('valuationsList.searchPlaceholder')}
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
        <DataGrid<ValuationRead>
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
          error={isError ? t('valuationsList.loadError') : null}
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
            icon: <CarFront size={24} />,
            title: t('valuationsList.emptyState.title'),
            description: t('valuationsList.emptyState.description'),
            action: (
              <Button component={Link} to="/valuations/new">
                {t('valuationsList.newValuation')}
              </Button>
            ),
          }}
          emptyFilteredState={{
            icon: <CarFront size={24} />,
            title: t('valuationsList.emptyFilteredState.title'),
            description: t('valuationsList.emptyFilteredState.description'),
            action: (
              <Button variant="default" onClick={() => setQuery('')}>
                {t('valuationsList.emptyFilteredState.action')}
              </Button>
            ),
          }}
          rowActions={(row) => {
            const menu = buildValuationRowMenu(t, row, {
              onRevalue: () => navigate(`/valuations/new?supersedes=${row.id}`),
              onMarkUsed: () => void markUsed(row),
            })
            return { navigate: [{ label: t('valuationsList.rowActions.open'), icon: <ExternalLink size={16} />, onClick: () => navigate(rowHref(row)) }], ...menu.overflow }
          }}
        />
      </OverviewShellRegion>
    </Stack>
  )
}
