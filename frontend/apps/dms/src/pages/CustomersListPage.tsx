import { useMemo, useState } from 'react'
import { Link, useNavigate } from 'react-router-dom'
import { Button, Group, Stack, Title } from '@mantine/core'
import { useDebouncedValue } from '@mantine/hooks'
import { useInfiniteQuery } from '@tanstack/react-query'
import { Copy, ExternalLink, Users } from 'lucide-react'
import { useTranslation } from 'react-i18next'
import {
  ActionBar,
  ColumnConfigPanel,
  CustomerTypeBadge,
  DataGrid,
  LanguageBadge,
  LifecycleStatusBadge,
  OverviewShellRegion,
  SelectionBar,
  ViewsAndFilters,
  resolveRelativeDateRange,
  useSetBreadcrumb,
  type ColumnRegistryEntry,
  type DateCondition,
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
  CANTON_OPTIONS,
  translatedCustomerTypeLabel,
  translatedCustomerTypeOptions,
  translatedLanguageOptions,
  translatedLifecycleLabel,
  translatedLifecycleOptions,
  translatedSourceOptions,
} from '../customerOptions'
import { formatDate } from '../utils/format'
import { customerName } from '../utils/customer'
import type { CustomerPage, CustomerRead } from '../api/types'

const GRID_KEY = 'mdm.customers.list'
const DEFAULT_SORT: SortSpec[] = [{ field: 'updatedAt', direction: 'desc' }]

/**
 * § Action Bar — Filter Builder's own field list "derived from the grid's
 * own columns." A hand-declared list rather than something mechanically
 * generated from `GridColumnDef` — `GridColumnMeta` has no filter-type
 * metadata yet, and adding it is a bigger cross-cutting change than this
 * one screen's wiring should carry; flagged here as the honest next step,
 * not silently worked around.
 */
function buildFilterFields(t: (key: string) => string): FilterFieldDef[] {
  return [
    { id: 'customerType', label: t('customersList.columns.type'), type: 'select', options: translatedCustomerTypeOptions(t) },
    { id: 'lifecycleStatus', label: t('customersList.columns.status'), type: 'select', options: translatedLifecycleOptions(t) },
    { id: 'language', label: t('customersList.columns.language'), type: 'select', options: translatedLanguageOptions(t) },
    { id: 'canton', label: t('customersList.filters.canton'), type: 'select', options: CANTON_OPTIONS },
    { id: 'updatedAt', label: t('customersList.columns.changed'), type: 'date' },
  ]
}

/**
 * `/customers` accepts five fixed, independent query parameters — no
 * generic predicate engine exists server-side. Two real gaps, both left
 * unsent rather than silently misfiltering:
 * - a `select` field's "is not" condition has no backend equivalent (the
 *   API only ever accepts a single equality value per field);
 * - `updatedAt`'s `moreThanDaysAgo` resolves to an upper bound
 *   (`resolveRelativeDateRange`'s own `to`), and `updated_since` is a
 *   lower bound only — there is no "changed before" parameter at all.
 */
function applyPredicatesToParams(params: URLSearchParams, predicates: FilterPredicate[]) {
  const EQUALITY_PARAM: Record<string, string> = {
    customerType: 'customer_type',
    lifecycleStatus: 'lifecycle_status',
    language: 'language',
    canton: 'canton',
  }
  for (const predicate of predicates) {
    const paramName = EQUALITY_PARAM[predicate.fieldId]
    if (paramName && predicate.condition === 'is' && typeof predicate.value === 'string') {
      params.set(paramName, predicate.value)
    }
    if (predicate.fieldId === 'updatedAt' && predicate.condition !== 'moreThanDaysAgo') {
      const range = resolveRelativeDateRange(predicate.condition as DateCondition, predicate.days, new Date())
      if (range.from) params.set('updated_since', range.from)
    }
  }
}

export function CustomersListPage() {
  const { t, i18n } = useTranslation()
  const locale = toSwissLocale(i18n.language as SupportedLanguage)
  useSetBreadcrumb([t('shell.nav.masterData'), t('shell.nav.customers')])
  const navigate = useNavigate()
  const { density, setDensity } = useUiPreferencesContext()
  const gridPrefs = useGridPreferences(GRID_KEY, { sort: DEFAULT_SORT })
  const savedViews = useSavedViews(GRID_KEY)

  const [query, setQuery] = useState('')
  const [debouncedQuery] = useDebouncedValue(query, 250)
  const [predicates, setPredicates] = useState<FilterPredicate[]>([])
  const [appliedViewId, setAppliedViewId] = useState<string | null>(null)
  const [selectedIds, setSelectedIds] = useState<Set<string>>(new Set())
  const sort = gridPrefs.sort

  const sortParam = sort.length > 0 ? sort.map((s) => `${s.field}:${s.direction}`).join(',') : undefined

  const { data, fetchNextPage, hasNextPage, isFetchingNextPage, isLoading, isError, refetch, isRefetching } =
    useInfiniteQuery({
      queryKey: ['customers', GRID_KEY, debouncedQuery, sortParam, predicates],
      queryFn: async ({ pageParam }: { pageParam: string | null }) => {
        const params = new URLSearchParams()
        if (debouncedQuery) params.set('q', debouncedQuery)
        if (sortParam) params.set('sort', sortParam)
        applyPredicatesToParams(params, predicates)
        params.set('limit', '50')
        if (pageParam) params.set('cursor', pageParam)
        return api.get<CustomerPage>(`/customers?${params.toString()}`)
      },
      initialPageParam: null as string | null,
      getNextPageParam: (lastPage) => lastPage.nextCursor,
    })

  const rows = useMemo(() => data?.pages.flatMap((page) => page.items) ?? [], [data])
  const total = data?.pages[0]?.total ?? null
  const totalIsEstimate = data?.pages[0]?.totalIsEstimate ?? false

  const filterFields = useMemo(() => buildFilterFields(t), [t])

  const columns: GridColumnDef<CustomerRead>[] = useMemo(
    () => [
      {
        id: 'customerNumber',
        header: t('customersList.columns.customerNumber'),
        cell: ({ row }) => row.original.customerNumber,
        meta: { sortField: 'customerNumber', pinned: 'left', mono: true, locked: true },
      },
      {
        id: 'name',
        header: t('customersList.columns.name'),
        cell: ({ row }) => <span style={{ fontWeight: 600 }}>{customerName(row.original)}</span>,
        meta: { sortField: 'lastName', locked: true },
      },
      {
        id: 'customerType',
        header: t('customersList.columns.type'),
        cell: ({ row }) => <CustomerTypeBadge type={row.original.customerType} label={translatedCustomerTypeLabel(t, row.original.customerType)} />,
      },
      {
        id: 'language',
        header: t('customersList.columns.language'),
        cell: ({ row }) => <LanguageBadge language={row.original.language} />,
      },
      {
        id: 'lifecycleStatus',
        header: t('customersList.columns.status'),
        cell: ({ row }) => <LifecycleStatusBadge status={row.original.lifecycleStatus} label={translatedLifecycleLabel(t, row.original.lifecycleStatus)} />,
      },
      {
        id: 'updatedAt',
        header: t('customersList.columns.changed'),
        cell: ({ row }) => formatDate(row.original.updatedAt, locale),
        meta: { sortField: 'updatedAt', align: 'right' },
      },
      // § ADR-060 — "every persisted field is available as a grid column."
      // Three more of CustomerRead's own fields, hidden by default (a
      // documented subset is visible, not "some fields can never be a
      // column") — proof that ColumnConfigPanel's show/hide is showing
      // something real, not window dressing on a fixed six-column grid.
      {
        id: 'canton',
        header: t('customersList.filters.canton'),
        cell: ({ row }) => row.original.address?.canton ?? '—',
        meta: { defaultVisible: false },
      },
      {
        id: 'source',
        header: t('customerDetail.overview.fields.source'),
        cell: ({ row }) => (row.original.source ? translatedSourceOptions(t).find((o) => o.value === row.original.source)?.label : '—'),
        meta: { defaultVisible: false },
      },
      {
        id: 'marketingConsent',
        header: t('customerDetail.overview.fields.marketingConsent'),
        cell: ({ row }) => (row.original.marketingConsent ? '✓' : '—'),
        meta: { defaultVisible: false, align: 'right' },
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
  const currentViewName = (appliedViewId && savedViews.views.find((v) => v.id === appliedViewId)?.name) || t('customersList.allCustomersView')

  const applyView = (view: SavedView) => {
    setAppliedViewId(view.id)
    if (view.snapshot.sort) gridPrefs.setSort(view.snapshot.sort)
    if (view.snapshot.columnLayout) gridPrefs.setColumnLayout(view.snapshot.columnLayout)
    if (view.snapshot.filters) setPredicates(view.snapshot.filters)
  }

  return (
    <Stack gap="md">
      <Group justify="space-between">
        <Title order={2}>{t('customersList.title')}</Title>
        <Button component={Link} to="/customers/new">
          {t('customersList.newCustomer')}
        </Button>
      </Group>

      <OverviewShellRegion
        header={<div />}
        actionBar={
          <>
            <ActionBar
              searchValue={query}
              onSearchChange={setQuery}
              searchPlaceholder={t('customersList.searchPlaceholder')}
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
              columnsSlot={<ColumnConfigPanel registry={columnRegistry} layout={gridPrefs.columnLayout ?? { order: columnRegistry.map((c) => c.id), hidden: columnRegistry.filter((c) => !c.defaultVisible).map((c) => c.id), widths: {}, pinnedLeft: [] }} onLayoutChange={gridPrefs.setColumnLayout} />}
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

            {selectedIds.size > 0 ? (
              <SelectionBar
                count={selectedIds.size}
                onClear={() => setSelectedIds(new Set())}
                countLabel={(n) => t('customersList.selection.count', { count: n })}
                clearLabel={t('customersList.selection.clear')}
                actions={[
                  {
                    // A minimal, honest bulk action proving the contract —
                    // not a real CSV export, which is a bigger feature
                    // this screen doesn't own (out of WP-6c's scope:
                    // presentation, not new backend/export capability).
                    label: t('customersList.selection.copyIds'),
                    icon: <Copy size={14} />,
                    onClick: () => navigator.clipboard.writeText([...selectedIds].join(', ')),
                  },
                ]}
              />
            ) : null}
          </>
        }
      >
        <DataGrid<CustomerRead>
          columns={columns}
          rows={rows}
          getRowId={(row) => row.id}
          sort={sort}
          onSortChange={gridPrefs.setSort}
          density={density}
          rowHref={(row) => `/customers/${row.id}`}
          linkComponent={Link}
          loading={isLoading}
          refetching={isRefetching && !isLoading}
          fetchingNextPage={isFetchingNextPage}
          hasNextPage={Boolean(hasNextPage)}
          onLoadMore={() => fetchNextPage()}
          error={isError ? 'Failed to load customers.' : null}
          onRetry={() => refetch()}
          total={total}
          totalIsEstimate={totalIsEstimate}
          isFiltered={isFiltered}
          locale={locale}
          selection={{ selectedIds, onSelectionChange: setSelectedIds }}
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
            icon: <Users size={24} />,
            title: t('customersList.emptyState.title'),
            description: t('customersList.emptyState.description'),
            action: (
              <Button component={Link} to="/customers/new">
                {t('customersList.newCustomer')}
              </Button>
            ),
          }}
          emptyFilteredState={{
            icon: <Users size={24} />,
            title: t('customersList.emptyFilteredState.title'),
            description: t('customersList.emptyFilteredState.description'),
            action: (
              <Button
                variant="default"
                onClick={() => {
                  setQuery('')
                  setPredicates([])
                  setAppliedViewId(null)
                }}
              >
                {t('customersList.emptyFilteredState.action')}
              </Button>
            ),
          }}
          rowActions={(row) => ({
            navigate: [
              {
                label: t('customersList.rowActions.open'),
                icon: <ExternalLink size={16} />,
                onClick: () => navigate(`/customers/${row.id}`),
              },
            ],
            // "Copy customer number" gets something out of the record for
            // use elsewhere — the same spirit as Export/print, not its own
            // sixth group (§ ADR-061 names exactly five).
            exportPrint: [
              {
                label: t('customersList.rowActions.copyCustomerNumber'),
                icon: <Copy size={16} />,
                onClick: () => navigator.clipboard.writeText(row.customerNumber),
              },
            ],
          })}
        />
      </OverviewShellRegion>
    </Stack>
  )
}
