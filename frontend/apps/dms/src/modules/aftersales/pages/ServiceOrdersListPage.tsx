import { useEffect, useMemo, useState } from 'react'
import { Link, useNavigate, useSearchParams } from 'react-router-dom'
import { Button, Group, Stack, Title } from '@mantine/core'
import { useDebouncedValue } from '@mantine/hooks'
import { useInfiniteQuery, useQuery, useQueryClient } from '@tanstack/react-query'
import { Plus, Wrench } from 'lucide-react'
import { useTranslation } from 'react-i18next'
import {
  ActionBar,
  ColumnConfigPanel,
  type ColumnRegistryEntry,
  DataGrid,
  defaultColumnLayout,
  deriveFilterFields,
  type FilterPredicate,
  type GridColumnDef,
  offeredPredicates,
  OverviewShellRegion,
  type SavedView,
  type SortSpec,
  sortSpecFromQueryParam,
  sortSpecToQueryParam,
  useSetBreadcrumb,
  ViewsAndFilters,
} from '@nexotec/ui-kit'
import { api } from '../../../api/client'
import type { GridSchemaRead, ServiceOrderPage, ServiceOrderSummary } from '../../../api/types'
import { useCanWrite } from '../../../auth/useCanWrite'
import { useActionBarLabels } from '../../../hooks/useActionBarLabels'
import { useColumnPanelLabels } from '../../../hooks/useColumnPanelLabels'
import { useDataGridLabels } from '../../../hooks/useDataGridLabels'
import { useGridPreferences } from '../../../hooks/useGridPreferences'
import { useSavedViews } from '../../../hooks/useSavedViews'
import { useUiPreferencesContext } from '../../../hooks/UiPreferencesContext'
import { useViewsAndFiltersLabels } from '../../../hooks/useViewsAndFiltersLabels'
import { toSwissLocale, type SupportedLanguage } from '../../../i18n'
import { sortableFieldsOf } from '../../../shared/gridSchema'
import { formatDateOnly, formatDateTime } from '../../../utils/format'
import { CancelServiceOrderDialog } from '../components/CancelServiceOrderDialog'
import { OrderStateBadge } from '../components/OrderStateBadge'
import { ServiceOrderCreateDialog } from '../components/ServiceOrderCreateDialog'
import { buildServiceOrderRowMenu } from '../components/serviceOrderRowMenu'
import { applyServiceOrderFilters, serviceOrderHref, translatedStateOptions } from '../serviceOrderOptions'

const GRID_KEY = 'serviceOrders.list'
const DEFAULT_SORT: SortSpec[] = [{ field: 'createdAt', direction: 'desc' }]

function parseFiltersParam(raw: string): FilterPredicate[] {
  try {
    const parsed: unknown = JSON.parse(raw)
    return Array.isArray(parsed) ? (parsed as FilterPredicate[]) : []
  } catch {
    // A hand-edited or truncated URL opens with no filters rather than failing.
    return []
  }
}

/**
 * D1 — the workshop orders (KAN-810 criterion 6). Server-side search and
 * cursor paging over `GET /v1/service-orders`; one filter, the order's state
 * (Anto, 2026-11-26: the chips of the slice page come each with the ticket
 * that builds its fact, so this list has none yet). Search, filter and sort
 * live in the URL (ADR-056). KAN-848: the list is on the list-query layer
 * (ADR-179) — every column sorts server-side, the sortable headers and the
 * default visibility are the served schema's (`…/service-orders/grid-schema`),
 * a column's id is its schema field. The filter stays the state alone, with
 * the options of Anto's ruling (`FILTER_STATES`), through `state`.
 */
export function ServiceOrdersListPage() {
  const { t, i18n } = useTranslation()
  const locale = toSwissLocale(i18n.language as SupportedLanguage)
  useSetBreadcrumb([t('shell.nav.orders')])
  const navigate = useNavigate()
  const queryClient = useQueryClient()
  const canWrite = useCanWrite('service_orders')
  const dataGridLabels = useDataGridLabels()
  const columnPanelLabels = useColumnPanelLabels()
  const viewsAndFiltersLabels = useViewsAndFiltersLabels()
  const actionBarLabels = useActionBarLabels()
  const { density, setDensity } = useUiPreferencesContext()
  const gridPrefs = useGridPreferences(GRID_KEY, { sort: DEFAULT_SORT })
  const savedViews = useSavedViews(GRID_KEY)
  const [searchParams, setSearchParams] = useSearchParams()
  const [createOpen, setCreateOpen] = useState(false)
  const [cancelOf, setCancelOf] = useState<ServiceOrderSummary | null>(null)
  const [appliedViewId, setAppliedViewId] = useState<string | null>(null)

  // The served registry: which headers sort, which columns show by default. Fetched once; the
  // list waits for it, so a link's sort is neither dropped nor sent unchecked meanwhile.
  const { data: gridSchema, isError: schemaFailed, refetch: refetchSchema } = useQuery({
    queryKey: ['grid-schema', 'service-orders'],
    queryFn: () => api.get<GridSchemaRead>('/service-orders/grid-schema'),
    staleTime: Infinity,
  })
  const sortableFields = useMemo(() => sortableFieldsOf(gridSchema), [gridSchema])
  // A sort from the URL (hand-edited, or a stale link) keeps only the fields the API sorts by.
  const sort = (searchParams.get('sort') ? sortSpecFromQueryParam(searchParams.get('sort')) : gridPrefs.sort).filter((s) =>
    sortableFields.has(s.field),
  )
  const urlPredicates = searchParams.get('filters') ? parseFiltersParam(searchParams.get('filters')!) : []

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
      { replace: true },
    )
  }
  const setSort = (next: SortSpec[]) => {
    gridPrefs.setSort(next)
    updateUrl({ sort: sortSpecToQueryParam(next) ?? null })
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

  const columns: GridColumnDef<ServiceOrderSummary>[] = useMemo(
    () => [
      {
        id: 'orderNumber',
        header: t('serviceOrders.columns.orderNumber'),
        cell: ({ row }) => row.original.orderNumber,
        meta: { pinned: 'left', mono: true, locked: true },
      },
      {
        id: 'vehicleLabel',
        header: t('serviceOrders.columns.vehicleAndContact'),
        // § Composite cells: the vehicle, then the ordering party as the secondary fact. It sorts by
        // the vehicle; the ordering party sorts in its own column.
        cell: ({ row }) => row.original.vehicleLabel,
        meta: { secondary: (row) => row.contact.label },
      },
      {
        id: 'contactLabel',
        header: t('serviceOrders.columns.contact'),
        cell: ({ row }) => row.original.contact.label,
      },
      {
        id: 'state',
        header: t('serviceOrders.columns.state'),
        cell: ({ row }) => <OrderStateBadge state={row.original.state} />,
        meta: { filter: { type: 'select', param: 'state', options: translatedStateOptions(t), conditions: ['is'] } },
      },
      {
        id: 'orderDate',
        header: t('serviceOrders.columns.orderDate'),
        cell: ({ row }) => formatDateOnly(row.original.orderDate, locale),
        meta: { align: 'right' },
      },
      {
        id: 'locationLabel',
        header: t('serviceOrders.columns.location'),
        cell: ({ row }) => row.original.locationLabel,
      },
      {
        id: 'createdAt',
        header: t('serviceOrders.columns.createdAt'),
        cell: ({ row }) => formatDateTime(row.original.createdAt, locale),
        meta: { align: 'right' },
      },
    ],
    [t, locale],
  )

  const filterFields = useMemo(() => deriveFilterFields(columns), [columns])
  // KAN-458: a predicate from the URL or a saved view this grid does not offer is neither sent nor shown.
  const predicates = offeredPredicates(urlPredicates, filterFields)

  // Default visibility is the served registry's — a column it does not show by default is one click away.
  const defaultVisible = useMemo(() => new Map((gridSchema?.fields ?? []).map((f) => [f.field, f.defaultVisible])), [gridSchema])
  const columnRegistry: ColumnRegistryEntry[] = useMemo(
    () =>
      columns.map((c) => ({
        id: String(c.id),
        label: typeof c.header === 'string' ? c.header : String(c.id),
        defaultVisible: defaultVisible.get(String(c.id)) ?? false,
        locked: c.meta?.locked,
      })),
    [columns, defaultVisible],
  )
  const columnLayout = useMemo(
    () => gridPrefs.columnLayout ?? defaultColumnLayout(columnRegistry),
    [gridPrefs.columnLayout, columnRegistry],
  )

  const sortParam = sortSpecToQueryParam(sort)
  const { data, fetchNextPage, hasNextPage, isFetchingNextPage, isLoading, isError, refetch, isRefetching } = useInfiniteQuery({
    queryKey: ['service-orders', GRID_KEY, debouncedQuery, sortParam, predicates],
    queryFn: async ({ pageParam }: { pageParam: string | null }) => {
      const params = new URLSearchParams()
      if (debouncedQuery) params.set('q', debouncedQuery)
      if (sortParam) params.set('sort', sortParam)
      applyServiceOrderFilters(params, predicates, filterFields)
      params.set('limit', '50')
      if (pageParam) params.set('cursor', pageParam)
      return api.get<ServiceOrderPage>(`/service-orders?${params.toString()}`)
    },
    initialPageParam: null as string | null,
    getNextPageParam: (lastPage) => lastPage.nextCursor,
    enabled: gridSchema !== undefined,
  })

  const rows = useMemo(() => data?.pages.flatMap((page) => page.items) ?? [], [data])
  const total = data?.pages[0]?.total ?? null
  const totalIsEstimate = data?.pages[0]?.totalIsEstimate ?? false
  const isFiltered = debouncedQuery.length > 0 || predicates.length > 0
  const currentViewName = (appliedViewId && savedViews.views.find((v) => v.id === appliedViewId)?.name) || t('serviceOrders.allView')

  const applyView = (view: SavedView) => {
    setAppliedViewId(view.id)
    if (view.snapshot.sort) setSort(view.snapshot.sort)
    if (view.snapshot.columnLayout) gridPrefs.setColumnLayout(view.snapshot.columnLayout)
    if (view.snapshot.filters) setPredicates(view.snapshot.filters)
  }

  const newOrderButton = canWrite ? (
    <Button leftSection={<Plus size={16} />} onClick={() => setCreateOpen(true)}>
      {t('serviceOrders.newOrder')}
    </Button>
  ) : undefined

  return (
    <Stack gap="md">
      <Group justify="space-between">
        <Title order={2}>{t('serviceOrders.title')}</Title>
        {newOrderButton}
      </Group>

      <OverviewShellRegion
        header={<div />}
        actionBar={
          <ActionBar
            labels={actionBarLabels}
            searchValue={query}
            onSearchChange={setQuery}
            searchPlaceholder={t('serviceOrders.searchPlaceholder')}
            density={density}
            onDensityChange={setDensity}
            onRefresh={() => refetch()}
            refreshing={isRefetching}
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
              <ColumnConfigPanel registry={columnRegistry} layout={columnLayout} onLayoutChange={gridPrefs.setColumnLayout} labels={columnPanelLabels} />
            }
          />
        }
      >
        <DataGrid<ServiceOrderSummary>
          columns={columns}
          rows={rows}
          getRowId={(row) => row.id}
          sort={sort}
          onSortChange={setSort}
          sortableFields={sortableFields}
          density={density}
          rowHref={(row) => serviceOrderHref(row.id)}
          linkComponent={Link}
          // A failed schema read is said, never an endless skeleton (KAN-848 review).
          loading={!schemaFailed && (isLoading || gridSchema === undefined)}
          refetching={isRefetching && !isLoading}
          fetchingNextPage={isFetchingNextPage}
          hasNextPage={Boolean(hasNextPage)}
          onLoadMore={() => fetchNextPage()}
          error={isError || schemaFailed ? t('serviceOrders.loadError') : null}
          onRetry={() => (schemaFailed ? void refetchSchema() : void refetch())}
          total={total}
          totalIsEstimate={totalIsEstimate}
          isFiltered={isFiltered}
          columnLayout={columnLayout}
          onColumnLayoutChange={gridPrefs.setColumnLayout}
          labels={dataGridLabels}
          emptyState={{
            icon: <Wrench size={24} />,
            title: t('serviceOrders.emptyState.title'),
            description: t('serviceOrders.emptyState.description'),
            action: newOrderButton,
          }}
          emptyFilteredState={{
            icon: <Wrench size={24} />,
            title: t('serviceOrders.emptyFilteredState.title'),
            description: t('serviceOrders.emptyFilteredState.description'),
            action: (
              <Button
                variant="default"
                onClick={() => {
                  setQuery('')
                  setPredicates([])
                }}
              >
                {t('serviceOrders.emptyFilteredState.action')}
              </Button>
            ),
          }}
          rowActions={(row) =>
            buildServiceOrderRowMenu(t, row, {
              onOpen: () => navigate(serviceOrderHref(row.id)),
              onCancel: canWrite ? () => setCancelOf(row) : undefined,
            })
          }
        />
      </OverviewShellRegion>

      <ServiceOrderCreateDialog
        opened={createOpen}
        onClose={() => setCreateOpen(false)}
        onCreated={(order) => {
          setCreateOpen(false)
          void queryClient.invalidateQueries({ queryKey: ['service-orders'] })
          navigate(serviceOrderHref(order.id))
        }}
      />
      <CancelServiceOrderDialog
        opened={cancelOf !== null}
        order={cancelOf}
        onClose={() => setCancelOf(null)}
        onCancelled={() => {
          setCancelOf(null)
          void queryClient.invalidateQueries({ queryKey: ['service-orders'] })
        }}
        onReload={() => void refetch()}
      />
    </Stack>
  )
}
