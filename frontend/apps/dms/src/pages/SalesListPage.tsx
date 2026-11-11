import { useEffect, useMemo, useState } from 'react'
import { Link, useNavigate, useSearchParams } from 'react-router-dom'
import { Alert, Badge, Button, Group, Stack, Title } from '@mantine/core'
import { useDebouncedValue } from '@mantine/hooks'
import { useInfiniteQuery } from '@tanstack/react-query'
import { Copy, ExternalLink, Handshake } from 'lucide-react'
import { useTranslation } from 'react-i18next'
import {
  ActionBar,
  ColumnConfigPanel,
  defaultColumnLayout,
  DataGrid,
  OverviewShellRegion,
  SalesStatusBadge,
  SalesTypeBadge,
  SelectionBar,
  ViewsAndFilters,
  useSetBreadcrumb,
  type ColumnRegistryEntry,
  type GridColumnDef,
  type SortSpec,
} from '@nexotec/ui-kit'
import { useUiPreferencesContext } from '../hooks/UiPreferencesContext'
import { useGridPreferences } from '../hooks/useGridPreferences'
import { useColumnPanelLabels } from '../hooks/useColumnPanelLabels'
import { useViewsAndFiltersLabels } from '../hooks/useViewsAndFiltersLabels'
import { useSavedViews } from '../hooks/useSavedViews'
import { api } from '../api/client'
import { toSwissLocale, type SupportedLanguage } from '../i18n'
import { translatedSalesDealStatusLabel, translatedSalesEntityTypeLabel } from '../salesOptions'
import { formatDate, formatCurrencyChf } from '../utils/format'
import type { SalesDealPage, SalesDealRead } from '../api/types'
import { useActionBarLabels } from '../hooks/useActionBarLabels'
import { useDataGridLabels } from '../hooks/useDataGridLabels'
import { buildDealTakeOverAction, useTakeOverDeal } from '../components/dealRowMenu'

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
  const dataGridLabels = useDataGridLabels()
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
  // KAN-200 (FR-A-37): the unassigned queue — deals whose seller left and
  // were handed to nobody — is one more equality predicate in the URL.
  const unassigned = searchParams.get('owner') === 'unassigned'
  // KAN-553 (ADR-115): deals whose stock car changed after they were priced —
  // one more equality predicate in the URL, applied by the server.
  const vehicleChanged = searchParams.get('vehicle') === 'changed'

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
  // KAN-6 — this grid had no selection at all, yet the reference
  // prototype's own equivalent screen defined bulk actions on top of it
  // (unreachable, since nothing could ever be selected). Same minimal,
  // honest "copy IDs" action as Customers'/Vehicles' own precedent —
  // proving the contract, not a real export feature this screen doesn't
  // own — now genuinely reachable because rows are actually selectable.
  const [selectedIds, setSelectedIds] = useState<Set<string>>(new Set())
  const sortParam = sort.length > 0 ? sort.map((s) => `${s.field}:${s.direction}`).join(',') : undefined

  const { data, fetchNextPage, hasNextPage, isFetchingNextPage, isLoading, isError, refetch, isRefetching } =
    useInfiniteQuery({
      queryKey: ['sales-deals', GRID_KEY, debouncedQuery, sortParam, dealType, unassigned, vehicleChanged],
      queryFn: async ({ pageParam }: { pageParam: string | null }) => {
        const params = new URLSearchParams()
        if (debouncedQuery) params.set('q', debouncedQuery)
        if (sortParam) params.set('sort', sortParam)
        if (dealType !== 'all') params.set('entityType', dealType)
        if (unassigned) params.set('unassigned', 'true')
        if (vehicleChanged) params.set('vehicleChanged', 'true')
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
        cell: ({ row }) => <SalesTypeBadge entityType={row.original.entityType} label={translatedSalesEntityTypeLabel(t, row.original.entityType)} />,
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
        // KAN-570: the stock car's number (the live entity's KAN-157 copy) —
        // optional, hidden by default (ADR-060: a stored field is a column).
        id: 'stockItemLabel',
        header: t('salesList.columns.stockNumber'),
        cell: ({ row }) => row.original.stockItemLabel ?? '—',
        meta: { sortField: 'stockItemLabel', mono: true, defaultVisible: false },
      },
      {
        id: 'grossPrice',
        header: t('salesList.columns.grossPrice'),
        cell: ({ row }) =>
          row.original.grossPrice != null ? formatCurrencyChf(Number(row.original.grossPrice)) : '—',
        meta: { sortField: 'grossPrice', align: 'right' },
      },
      {
        // KAN-200: the responsible seller (rule 2's label; ADR-060: a stored
        // field is a column). "—" is the unassigned queue.
        id: 'ownerLabel',
        header: t('salesList.columns.owner'),
        cell: ({ row }) => row.original.ownerLabel ?? '—',
        meta: { sortField: 'ownerLabel' },
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
        // KAN-553 (ADR-115): the marker of a deal whose stock car changed
        // after it was priced (ADR-060: a stored field is a column).
        id: 'vehicleChanged',
        header: t('salesList.columns.vehicleChanged'),
        cell: ({ row }) =>
          row.original.vehicleChangedAt ? (
            <Badge color="yellow" variant="light" size="sm">
              {t('salesList.vehicleChangedMarker')}
            </Badge>
          ) : null,
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
  // A user with no saved layout sees the documented default subset, the
  // rest one click away (ADR-060): DataGrid without a layout would render
  // every column, defaultVisible ignored (KAN-361). Panel and grid read
  // this one value.
  const columnLayout = useMemo(
    () => gridPrefs.columnLayout ?? defaultColumnLayout(columnRegistry),
    [gridPrefs.columnLayout, columnRegistry]
  )
  const columnPanelLabels = useColumnPanelLabels()
  const viewsAndFiltersLabels = useViewsAndFiltersLabels()
  const actionBarLabels = useActionBarLabels()

  const isFiltered = debouncedQuery.length > 0
  const currentViewName = (appliedViewId && savedViews.views.find((v) => v.id === appliedViewId)?.name) || t('salesList.allDealsView')

  // KAN-405: «Übernehmen» — the deal's one definition, shared with the
  // offer and contract detail overflows (ADR-061).
  const takeOver = useTakeOverDeal()
  const takeOverRow = (row: SalesDealRead) =>
    void takeOver.takeOver(row.entityType, row.entityType === 'contract' ? (row.contractId ?? row.id) : (row.offerId ?? row.id))

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

      {takeOver.error && (
        <Alert color="red" role="alert" withCloseButton closeButtonLabel={t('common.dismiss')} onClose={takeOver.clearError}>
          {takeOver.error}
        </Alert>
      )}

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
        <Button
          variant={unassigned ? 'filled' : 'default'}
          size="xs"
          onClick={() => updateUrl({ owner: unassigned ? null : 'unassigned' })}
        >
          {t('salesList.chips.unassigned')}
        </Button>
        <Button
          variant={vehicleChanged ? 'filled' : 'default'}
          size="xs"
          onClick={() => updateUrl({ vehicle: vehicleChanged ? null : 'changed' })}
        >
          {t('salesList.chips.vehicleChanged')}
        </Button>
      </Group>

      <OverviewShellRegion
        header={<div />}
        actionBar={
          <>
            <ActionBar
              labels={actionBarLabels}
              searchValue={query}
              onSearchChange={setQuery}
              searchPlaceholder={t('salesList.searchPlaceholder')}
              density={density}
              onDensityChange={setDensity}
              onRefresh={() => refetch()}
              refreshing={isRefetching}
              filterSlot={
                <ViewsAndFilters
                  labels={viewsAndFiltersLabels}
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
                  layout={columnLayout}
                  onLayoutChange={gridPrefs.setColumnLayout}
                  labels={columnPanelLabels}
                />
              }
            />
            {selectedIds.size > 0 && (
              <SelectionBar
                count={selectedIds.size}
                onClear={() => setSelectedIds(new Set())}
                countLabel={(n) => t('salesList.selection.count', { count: n })}
                clearLabel={t('salesList.selection.clear')}
                actions={[
                  {
                    label: t('salesList.selection.copyIds'),
                    icon: <Copy size={14} />,
                    onClick: () => navigator.clipboard.writeText([...selectedIds].join(', ')),
                  },
                ]}
              />
            )}
          </>
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
          selection={{
            selectedIds,
            onSelectionChange: setSelectedIds,
            selectAllLabel: t('common.selectAllLabel'),
            selectRowLabel: (row) => t('common.selectRowLabel', { label: [row.number, row.customerLabel].filter(Boolean).join(' · ') }),
          }}
          loading={isLoading}
          refetching={isRefetching && !isLoading}
          fetchingNextPage={isFetchingNextPage}
          hasNextPage={Boolean(hasNextPage)}
          onLoadMore={() => fetchNextPage()}
          error={isError ? t('salesList.loadError') : null}
          onRetry={() => refetch()}
          total={total}
          totalIsEstimate={totalIsEstimate}
          isFiltered={isFiltered}
          columnLayout={columnLayout}
          onColumnLayoutChange={gridPrefs.setColumnLayout}
          labels={dataGridLabels}
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
            edit: [buildDealTakeOverAction(t, row, takeOver.currentUserId, () => takeOverRow(row))],
          })}
        />
      </OverviewShellRegion>
    </Stack>
  )
}
