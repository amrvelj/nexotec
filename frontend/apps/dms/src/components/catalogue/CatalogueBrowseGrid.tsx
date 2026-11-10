import { useCallback, useEffect, useMemo, useState } from 'react'
import { useSearchParams } from 'react-router-dom'
import { Alert, Group, Select, Stack } from '@mantine/core'
import { useDebouncedValue } from '@mantine/hooks'
import { useInfiniteQuery, useQuery } from '@tanstack/react-query'
import { CircleAlert, Copy, Database, Plug, ShieldOff, Unplug } from 'lucide-react'
import { useTranslation } from 'react-i18next'
import { formatCurrencyChf, formatNumber } from '../../utils/format'
import {
  ActionBar,
  ColumnConfigPanel,
  defaultColumnLayout,
  DataGrid,
  OverviewShellRegion,
  SelectionBar,
  ViewsAndFilters,
  offeredPredicates,
  type ColumnRegistryEntry,
  type FilterPredicate,
  type GridColumnDef,
  type SavedView,
  type SortSpec,
} from '@nexotec/ui-kit'
import { useUiPreferencesContext } from '../../hooks/UiPreferencesContext'
import { useGridPreferences } from '../../hooks/useGridPreferences'
import { useColumnPanelLabels } from '../../hooks/useColumnPanelLabels'
import { useViewsAndFiltersLabels } from '../../hooks/useViewsAndFiltersLabels'
import { useSavedViews } from '../../hooks/useSavedViews'
import { api } from '../../api/client'
import {
  applyCatalogueFilters,
  buildCatalogueFilterFields,
  CATALOGUE_FILTER_SCOPE,
  CATALOGUE_LABEL_LISTS,
  readLegacyCataloguePredicates,
} from '../../catalogueOptions'
import { fetchAllReferenceValues, LABEL_FIELD, resolveLanguage } from '../../hooks/useReferenceValueOptions'
import type {
  CatalogueBrandPage,
  CatalogueBrowseMode,
  CatalogueBrowseState,
  CatalogueFacetsRead,
  CatalogueModelGroupPage,
  CatalogueVariantPage,
  CatalogueVariantRead,
  ReferenceValueRead,
} from '../../api/types'
import { useActionBarLabels } from '../../hooks/useActionBarLabels'
import { useDataGridLabels } from '../../hooks/useDataGridLabels'

const GRID_KEY = 'mdm.catalogue.variants'
// The provider's own order; a column sorts server-side on the identAPI (KAN-548).
const DEFAULT_SORT: SortSpec[] = []
const DEFAULT_VEHICLE_KIND = 'passenger_car'
/** The fields the identAPI sorts on (KAN-548). A stored sort on another field —
 * the old default `variantName`, a user's `ps` or `basePrice` — is narrowed
 * away like an unoffered predicate (ADR-102), never sent as a 422. */
const SORTABLE_FIELDS = new Set(['kw', 'displacementCcm', 'co2Gkm', 'doors', 'seats'])
// Provider answers are kept for the session's work on a brand: no refetch on
// window focus and none while fresh, so a page the user already saw costs no
// second billed request (ADR-112 (4)).
const PROVIDER_QUERY = { staleTime: 5 * 60 * 1000, refetchOnWindowFocus: false } as const

function parseSortParam(raw: string): SortSpec[] {
  return raw
    .split(',')
    .map((part): SortSpec | null => {
      const [field, direction] = part.split(':')
      return field ? { field, direction: direction === 'desc' ? 'desc' : 'asc' } : null
    })
    .filter((s): s is SortSpec => s !== null)
}

function serializeSort(sort: SortSpec[]): string {
  return sort.map((s) => `${s.field}:${s.direction}`).join(',')
}

function parseFiltersParam(raw: string): FilterPredicate[] {
  try {
    const parsed: unknown = JSON.parse(raw)
    return Array.isArray(parsed) ? (parsed as FilterPredicate[]) : []
  } catch {
    return []
  }
}

/** Every faceted reference list, fetched once and merged into a
 * `(listCode, valueCode) -> label` lookup in the current UI language.
 *
 * KAN-239 — each list is paged within the API's cap (it once asked
 * `limit=200`, which the API answers 422) and retired values are included,
 * because a variant can still carry one. A list that failed to load is
 * `null`: its codes render as `⚠ <code>` and `labelsFailed` drives an alert.
 * A code missing from its list, or with no label in this language, renders
 * `⚠ <code>` too — never the raw code as if it were a label. */
function useReferenceLabels(): {
  labelFor: (listCode: string, valueCode: string) => string
  activeValuesOf: (listCode: string) => string[]
  labelsFailed: boolean
} {
  const { i18n } = useTranslation()
  const field = LABEL_FIELD[resolveLanguage(i18n.language)]
  const listCodes = useMemo(() => CATALOGUE_LABEL_LISTS, [])

  const results = useQuery({
    queryKey: ['catalogue-facet-labels', listCodes],
    queryFn: async (): Promise<Record<string, ReferenceValueRead[] | null>> => {
      const lists = await Promise.all(
        listCodes.map((code) =>
          fetchAllReferenceValues(code)
            .then((rows) => [code, rows] as const)
            .catch(() => [code, null] as const),
        ),
      )
      return Object.fromEntries(lists)
    },
  })

  const labelFor = useCallback(
    (listCode: string, valueCode: string): string => {
      // Still loading: the code stands in until the labels arrive.
      if (!results.data) return valueCode
      const label = results.data[listCode]?.find((v) => v.valueCode === valueCode)?.[field]
      return typeof label === 'string' && label.trim() !== '' ? label : `⚠ ${valueCode}`
    },
    [results.data, field],
  )

  const activeValuesOf = useCallback(
    (listCode: string): string[] =>
      (results.data?.[listCode] ?? []).filter((v) => v.active).map((v) => v.valueCode),
    [results.data],
  )

  return {
    labelFor,
    activeValuesOf,
    labelsFailed: results.data ? Object.values(results.data).some((rows) => rows === null) : false,
  }
}

export interface CatalogueBrowseGridProps {
  mode: CatalogueBrowseMode
  /** When set, a row activates (click / Enter) instead of just selecting —
   * the configurator overlay's "Find the car" phase (C-C) passes this to
   * pick a variant. Unset on the standalone `/catalogue` page: selection
   * only, no row navigation (a variant detail screen is C-E). */
  onSelectVariant?: (variant: CatalogueVariantRead) => void
}

/** What this grid applies of stored predicates (a link, a saved view): the
 * legacy «grösser/kleiner als» read as the inclusive bounds they were always
 * sent as, then narrowed to the catalogue's filter scope (ADR-102). The views
 * control asks the same function whether a view was narrowed (KAN-475) — the
 * facet-based builder fields are not the scope, and would mark a fully
 * applied view as changed while the facets load. */
function narrowCataloguePredicates(stored: FilterPredicate[]): FilterPredicate[] {
  return offeredPredicates(readLegacyCataloguePredicates(stored), CATALOGUE_FILTER_SCOPE)
}

/**
 * The catalogue results grid — the drill-down (vehicle kind → brand →
 * model group), the provider's facets, saved views, column config,
 * selection. **Live on the identAPI** (ADR-112, KAN-548): the brands, the
 * model groups, each facet field and each page of variants are one provider
 * request each, made only once the user has chosen enough — a brand at least
 * — so nothing is fetched for a page nobody asked for. The provider's states
 * (`notConfigured`, `notInContract`, `unavailable`) are alerts, never a
 * broken grid. Standalone on `/catalogue`, and embedded in the configurator
 * overlay's "Find the car" phase (C-C).
 */
export function CatalogueBrowseGrid({ mode, onSelectVariant }: CatalogueBrowseGridProps) {
  const { t } = useTranslation()
  const dataGridLabels = useDataGridLabels()
  const { density, setDensity } = useUiPreferencesContext()
  const gridPrefs = useGridPreferences(GRID_KEY, { sort: DEFAULT_SORT })
  const savedViews = useSavedViews(GRID_KEY)
  const [searchParams, setSearchParams] = useSearchParams()
  const { labelFor, activeValuesOf, labelsFailed } = useReferenceLabels()

  const vehicleKind = searchParams.get('kind') || DEFAULT_VEHICLE_KIND
  const brand = searchParams.get('brand') || null
  const modelGroupKey = searchParams.get('modelGroup') || null
  const sort = (searchParams.get('sort') ? parseSortParam(searchParams.get('sort')!) : gridPrefs.sort).filter((s) => SORTABLE_FIELDS.has(s.field))
  // KAN-458: a predicate from the URL or a saved view that this grid does
  // not offer is neither sent nor shown as an active filter.
  const predicates = narrowCataloguePredicates(searchParams.get('filters') ? parseFiltersParam(searchParams.get('filters')!) : [])

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
  const [selectedIds, setSelectedIds] = useState<Set<string>>(new Set())

  // Every provider query carries the drill-down scope; a brand (or a model
  // group) is the least the provider is asked with (ADR-112 (4)).
  const scopeParams = (extra: Record<string, string | null | undefined> = {}) => {
    const params = new URLSearchParams({ vehicleKind })
    if (brand) params.set('brand', brand)
    if (modelGroupKey) params.set('modelGroupKey', modelGroupKey)
    for (const [key, value] of Object.entries(extra)) if (value) params.set(key, value)
    return params
  }
  const scoped = brand !== null || modelGroupKey !== null

  const brandsQuery = useQuery({
    queryKey: ['catalogue-brands', vehicleKind],
    queryFn: () => api.get<CatalogueBrandPage>(`/catalogue/brands?vehicleKind=${encodeURIComponent(vehicleKind)}`),
    ...PROVIDER_QUERY,
  })
  // The mode's production-year default (PRD NurNeue) narrows the model groups;
  // the identAPI's variant rows carry no filterable production end.
  const modelGroupsQuery = useQuery({
    queryKey: ['catalogue-model-groups', vehicleKind, brand, mode],
    queryFn: () => api.get<CatalogueModelGroupPage>(`/catalogue/model-groups?${scopeParams({ mode }).toString()}`),
    enabled: brand !== null,
    ...PROVIDER_QUERY,
  })
  // One request per facet field, only once a brand is chosen **and the user
  // opened the views-and-filters control in this scope** (ADR-112 (4)): a
  // scope change — kind, brand, model group — costs its page and its model
  // groups, never four facet requests nobody looked at; the facets come when
  // the control is clicked open again.
  const facetsScope = `${vehicleKind}|${brand ?? ''}|${modelGroupKey ?? ''}`
  const [facetsOpenedFor, setFacetsOpenedFor] = useState<string | null>(null)
  // The memory is cleared the moment the scope changes — during this very
  // render (React's derived-state pattern), so the query never sees "opened"
  // for a scope the user has not clicked the control in. Coming back to a
  // scope opened earlier costs nothing until the next click, even once the
  // cached facets have gone stale.
  if (facetsOpenedFor !== null && facetsOpenedFor !== facetsScope) {
    setFacetsOpenedFor(null)
  }
  const facetsQuery = useQuery({
    queryKey: ['catalogue-facets', vehicleKind, brand, modelGroupKey],
    queryFn: () => api.get<CatalogueFacetsRead>(`/catalogue/facets?${scopeParams().toString()}`),
    enabled: brand !== null && facetsOpenedFor === facetsScope,
    ...PROVIDER_QUERY,
  })

  const sortParam = sort.length > 0 ? serializeSort(sort) : undefined
  const variantsQuery = useInfiniteQuery({
    queryKey: ['catalogue-variants', vehicleKind, brand, modelGroupKey, debouncedQuery, sortParam, predicates],
    queryFn: async ({ pageParam }: { pageParam: string | null }) => {
      const params = scopeParams({ q: debouncedQuery, sort: sortParam })
      applyCatalogueFilters(params, predicates)
      // `cursor` is the provider page number: the page the user moved the grid to.
      if (pageParam) params.set('cursor', pageParam)
      return api.get<CatalogueVariantPage>(`/catalogue/variants?${params.toString()}`)
    },
    initialPageParam: null as string | null,
    getNextPageParam: (lastPage) => lastPage.nextCursor,
    enabled: scoped,
    ...PROVIDER_QUERY,
  })

  // The next provider page is fetched only after the user has moved in the
  // grid (a wheel, a scroll, a key) since the last page arrived: the identAPI
  // pages at 10, so the grid's "within 10 rows of the end" prefetch (FR-UI-04)
  // would otherwise walk page after page with no scroll at all — the pages
  // nobody asked for that ADR-112 (4) forbids.
  const [scrolledSinceLastPage, setScrolledSinceLastPage] = useState(false)
  const armNextPage = () => setScrolledSinceLastPage(true)
  const loadNextPage = () => {
    setScrolledSinceLastPage(false)
    void variantsQuery.fetchNextPage()
  }
  // A movement counts for the query it happened in: a new brand, model group,
  // filter, sort or search, and every page that arrives, start from "not moved".
  const variantsKey = JSON.stringify([vehicleKind, brand, modelGroupKey, debouncedQuery, sortParam, predicates])
  const loadedPages = variantsQuery.data?.pages.length ?? 0
  useEffect(() => {
    setScrolledSinceLastPage(false)
  }, [variantsKey, loadedPages])

  const firstPage = variantsQuery.data?.pages[0]
  // The provider's state, from whichever query answered first: the brand
  // list speaks for a Betrieb without access before any variant was asked.
  const state: CatalogueBrowseState | null = firstPage?.state ?? brandsQuery.data?.state ?? null
  const browseAvailable = state === null || state === 'ok'
  const rows = useMemo(
    () => variantsQuery.data?.pages.flatMap((p) => p.items) ?? [],
    [variantsQuery.data],
  )
  const total = firstPage?.total ?? null
  const totalIsEstimate = firstPage?.totalIsEstimate ?? false

  const filterFields = useMemo(
    () => buildCatalogueFilterFields(t, facetsQuery.data, labelFor),
    [t, facetsQuery.data, labelFor],
  )

  const columns: GridColumnDef<CatalogueVariantRead>[] = useMemo(
    () => [
      {
        id: 'variant',
        header: t('catalogueBrowse.columns.variant'),
        cell: ({ row }) => <span style={{ fontWeight: 600 }}>{row.original.variantName}</span>,
        meta: { pinned: 'left', locked: true },
      },
      {
        id: 'brand',
        header: t('catalogueBrowse.columns.brand'),
        cell: ({ row }) => row.original.brandDisplayName,
      },
      {
        id: 'modelGroup',
        header: t('catalogueBrowse.columns.modelGroup'),
        cell: ({ row }) => row.original.modelGroupName,
      },
      {
        id: 'trim',
        header: t('catalogueBrowse.columns.trim'),
        cell: ({ row }) => row.original.spec.trimName ?? '—',
      },
      {
        id: 'fuelType',
        header: t('catalogueBrowse.columns.fuelType'),
        cell: ({ row }) => codeLabel(labelFor, 'fuel_type', row.original.fuelType),
      },
      {
        id: 'bodyStyle',
        header: t('catalogueBrowse.columns.bodyStyle'),
        cell: ({ row }) => codeLabel(labelFor, 'body_style', row.original.bodyStyle),
        meta: { defaultVisible: false },
      },
      {
        id: 'drivetrain',
        header: t('catalogueBrowse.columns.drivetrain'),
        cell: ({ row }) => codeLabel(labelFor, 'drivetrain', row.original.drivetrain),
        meta: { defaultVisible: false },
      },
      {
        id: 'transmission',
        header: t('catalogueBrowse.columns.transmission'),
        cell: ({ row }) => codeLabel(labelFor, 'transmission', row.original.transmission),
        meta: { defaultVisible: false },
      },
      {
        id: 'ps',
        header: t('catalogueBrowse.columns.ps'),
        cell: ({ row }) => (row.original.spec.ps != null ? formatNumber(row.original.spec.ps) : '—'),
        meta: { align: 'right' },
      },
      {
        id: 'kw',
        header: t('catalogueBrowse.columns.kw'),
        cell: ({ row }) => (row.original.spec.kw != null ? formatNumber(row.original.spec.kw) : '—'),
        meta: { sortField: 'kw', align: 'right', defaultVisible: false },
      },
      {
        id: 'displacementCcm',
        header: t('catalogueBrowse.columns.displacementCcm'),
        cell: ({ row }) =>
          row.original.spec.displacementCcm != null ? formatNumber(row.original.spec.displacementCcm) : '—',
        meta: { sortField: 'displacementCcm', align: 'right', defaultVisible: false },
      },
      {
        id: 'basePrice',
        header: t('catalogueBrowse.columns.basePrice'),
        cell: ({ row }) =>
          row.original.currentPrice ? formatChf(row.original.currentPrice.amount) : '—',
        meta: { align: 'right' },
      },
      {
        id: 'productionFrom',
        header: t('catalogueBrowse.columns.productionFrom'),
        cell: ({ row }) => row.original.modelYearFrom,
        meta: { align: 'right' },
      },
      {
        id: 'productionTo',
        header: t('catalogueBrowse.columns.productionTo'),
        cell: ({ row }) => row.original.modelYearTo ?? t('catalogueBrowse.columns.inProduction'),
        meta: { align: 'right' },
      },
      {
        id: 'typeApprovals',
        header: t('catalogueBrowse.columns.typeApprovals'),
        cell: ({ row }) => row.original.typeApprovalNumbers.join(', ') || '—',
        meta: { defaultVisible: false, mono: true },
      },
      {
        id: 'doors',
        header: t('catalogueBrowse.columns.doors'),
        cell: ({ row }) => row.original.spec.doors ?? '—',
        meta: { sortField: 'doors', align: 'right', defaultVisible: false },
      },
      {
        id: 'seats',
        header: t('catalogueBrowse.columns.seats'),
        cell: ({ row }) => row.original.spec.seats ?? '—',
        meta: { sortField: 'seats', align: 'right', defaultVisible: false },
      },
      {
        id: 'co2Gkm',
        header: t('catalogueBrowse.columns.co2Gkm'),
        cell: ({ row }) => row.original.spec.co2Gkm ?? '—',
        meta: { sortField: 'co2Gkm', align: 'right', defaultVisible: false },
      },
    ],
    [t, labelFor],
  )

  const columnRegistry: ColumnRegistryEntry[] = useMemo(
    () =>
      columns.map((c) => ({
        id: String(c.id),
        label: typeof c.header === 'string' ? c.header : String(c.id),
        defaultVisible: c.meta?.defaultVisible ?? true,
        locked: c.meta?.locked,
      })),
    [columns],
  )
  // A user with no saved layout sees the documented default subset, the
  // rest one click away (ADR-060): DataGrid without a layout would render
  // every column, defaultVisible ignored (KAN-361). Panel and grid read
  // this one value.
  const columnLayout = useMemo(
    () => gridPrefs.columnLayout ?? defaultColumnLayout(columnRegistry),
    [gridPrefs.columnLayout, columnRegistry],
  )
  const columnPanelLabels = useColumnPanelLabels()
  const viewsAndFiltersLabels = useViewsAndFiltersLabels()
  const actionBarLabels = useActionBarLabels()

  const isFiltered = debouncedQuery.length > 0 || predicates.length > 0 || modelGroupKey !== null
  const currentViewName =
    (appliedViewId && savedViews.views.find((v) => v.id === appliedViewId)?.name) ||
    t('catalogueBrowse.allVariantsView')

  const applyView = (view: SavedView) => {
    setAppliedViewId(view.id)
    if (view.snapshot.sort) setSort(view.snapshot.sort)
    if (view.snapshot.columnLayout) gridPrefs.setColumnLayout(view.snapshot.columnLayout)
    if (view.snapshot.filters) setPredicates(view.snapshot.filters)
  }

  const kindOptions = activeValuesOf('vehicle_kind').map((code) => ({ value: code, label: labelFor('vehicle_kind', code) }))
  const brandOptions = (brandsQuery.data?.items ?? []).map((b) => ({ value: b.name, label: b.name }))
  const modelGroupOptions = (modelGroupsQuery.data?.items ?? []).map((g) => ({ value: g.key, label: g.name }))

  const stateAlert =
    state === 'notConfigured' ? (
      <Alert icon={<Plug size={16} />} color="yellow" title={t('catalogueBrowse.notConfigured.title')} data-testid="catalogue-state-notConfigured">
        {t('catalogueBrowse.notConfigured.body')}
      </Alert>
    ) : state === 'notInContract' ? (
      <Alert icon={<ShieldOff size={16} />} color="yellow" title={t('catalogueBrowse.notInContract.title')} data-testid="catalogue-state-notInContract">
        {t('catalogueBrowse.notInContract.body')}
      </Alert>
    ) : state === 'unavailable' ? (
      <Alert icon={<Unplug size={16} />} color="yellow" title={t('catalogueBrowse.unavailable.title')} data-testid="catalogue-state-unavailable">
        {t('catalogueBrowse.unavailable.body')}
      </Alert>
    ) : null

  return (
    <Stack gap="md">
      <Group gap="sm" wrap="wrap">
        <Select
          aria-label={t('catalogueBrowse.drilldown.vehicleKind')}
          data={kindOptions.length > 0 ? kindOptions : [{ value: vehicleKind, label: labelFor('vehicle_kind', vehicleKind) }]}
          value={vehicleKind}
          onChange={(value) => updateUrl({ kind: value && value !== DEFAULT_VEHICLE_KIND ? value : null, brand: null, modelGroup: null })}
          allowDeselect={false}
          w={200}
        />
        <Select
          aria-label={t('catalogueBrowse.drilldown.brand')}
          placeholder={t('catalogueBrowse.drilldown.brandPlaceholder')}
          data={brandOptions}
          value={brand}
          onChange={(value) => updateUrl({ brand: value, modelGroup: null })}
          error={brandsQuery.isError ? t('catalogueBrowse.drilldown.brandsLoadError') : undefined}
          clearable
          searchable
          w={220}
        />
        <Select
          aria-label={t('catalogueBrowse.drilldown.modelGroup')}
          placeholder={
            brand
              ? t('catalogueBrowse.drilldown.modelGroupPlaceholder')
              : t('catalogueBrowse.drilldown.modelGroupNeedsBrand')
          }
          data={modelGroupOptions}
          value={modelGroupKey}
          onChange={(value) => updateUrl({ modelGroup: value })}
          disabled={!brand}
          clearable
          searchable
          w={240}
        />
      </Group>

      {labelsFailed && (
        <Alert icon={<CircleAlert size={16} />} color="red" title={t('catalogueBrowse.labelsLoadError.title')}>
          {t('catalogueBrowse.labelsLoadError.body')}
        </Alert>
      )}

      {stateAlert}

      <OverviewShellRegion
        header={<div />}
        actionBar={
          <>
            <ActionBar
              labels={actionBarLabels}
              searchValue={query}
              onSearchChange={setQuery}
              searchPlaceholder={t('catalogueBrowse.searchPlaceholder')}
              density={density}
              onDensityChange={setDensity}
              onRefresh={() => variantsQuery.refetch()}
              refreshing={variantsQuery.isRefetching}
              filterSlot={
                <div onClickCapture={() => setFacetsOpenedFor(facetsScope)}>
                  <ViewsAndFilters
                  labels={viewsAndFiltersLabels}
                  currentViewName={currentViewName}
                  views={savedViews.views}
                  onApplyView={applyView}
                  onSaveCurrentAsView={(name) =>
                    savedViews.saveView(name, {
                      sort,
                      columnLayout: gridPrefs.columnLayout ?? undefined,
                      filters: predicates,
                    })
                  }
                  onDeleteView={savedViews.deleteView}
                  onSetDefaultView={savedViews.setDefaultView}
                  fields={filterFields}
                  narrowPredicates={narrowCataloguePredicates}
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
                </div>
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
                countLabel={(n) => t('catalogueBrowse.selection.count', { count: n })}
                clearLabel={t('catalogueBrowse.selection.clear')}
                actions={[
                  {
                    label: t('catalogueBrowse.selection.copyIds'),
                    icon: <Copy size={14} />,
                    onClick: () => navigator.clipboard.writeText([...selectedIds].join(', ')),
                  },
                ]}
              />
            )}
          </>
        }
      >
        <div onWheelCapture={armNextPage} onScrollCapture={armNextPage} onKeyDownCapture={armNextPage} onTouchMoveCapture={armNextPage}>
        <DataGrid<CatalogueVariantRead>
          columns={columns}
          rows={browseAvailable ? rows : []}
          getRowId={(row) => row.id}
          sort={sort}
          onSortChange={setSort}
          density={density}
          onRowActivate={onSelectVariant}
          loading={scoped && variantsQuery.isLoading}
          refetching={variantsQuery.isRefetching && !variantsQuery.isLoading}
          fetchingNextPage={variantsQuery.isFetchingNextPage}
          hasNextPage={scrolledSinceLastPage && Boolean(variantsQuery.hasNextPage)}
          onLoadMore={loadNextPage}
          error={variantsQuery.isError ? t('catalogueBrowse.loadError') : null}
          onRetry={() => variantsQuery.refetch()}
          total={total}
          totalIsEstimate={totalIsEstimate}
          isFiltered={isFiltered}
          selection={{
            selectedIds,
            onSelectionChange: setSelectedIds,
            selectAllLabel: t('common.selectAllLabel'),
            selectRowLabel: (row) => t('common.selectRowLabel', { label: `${row.brandDisplayName} ${row.modelGroupName} ${row.variantName}` }),
          }}
          columnLayout={columnLayout}
          onColumnLayoutChange={gridPrefs.setColumnLayout}
          labels={dataGridLabels}
          emptyState={
            scoped
              ? {
                  icon: <Database size={24} />,
                  title: t('catalogueBrowse.emptyState.title'),
                  description: t('catalogueBrowse.emptyState.description'),
                }
              : {
                  icon: <Database size={24} />,
                  title: t('catalogueBrowse.needsBrand.title'),
                  description: t('catalogueBrowse.needsBrand.description'),
                }
          }
          emptyFilteredState={{
            icon: <Database size={24} />,
            title: t('catalogueBrowse.emptyFilteredState.title'),
            description: t('catalogueBrowse.emptyFilteredState.description'),
          }}
        />
        </div>
      </OverviewShellRegion>
    </Stack>
  )
}

function codeLabel(
  labelFor: (l: string, v: string) => string,
  listCode: string,
  valueCode: string | null | undefined,
): string {
  return valueCode ? labelFor(listCode, valueCode) : '—'
}

function formatChf(amount: string): string {
  const n = Number(amount)
  return Number.isFinite(n) ? formatCurrencyChf(n) : amount
}
