import { useEffect, useMemo, useState } from 'react'
import { useSearchParams } from 'react-router-dom'
import { Alert, Group, Select, Stack } from '@mantine/core'
import { useDebouncedValue } from '@mantine/hooks'
import { useInfiniteQuery, useQuery } from '@tanstack/react-query'
import { Copy, Database, Plug } from 'lucide-react'
import { useTranslation } from 'react-i18next'
import {
  ActionBar,
  ColumnConfigPanel,
  DataGrid,
  OverviewShellRegion,
  SelectionBar,
  ViewsAndFilters,
  type ColumnRegistryEntry,
  type FilterPredicate,
  type GridColumnDef,
  type SavedView,
  type SortSpec,
} from '@nexotec/ui-kit'
import { useUiPreferencesContext } from '../../hooks/UiPreferencesContext'
import { useGridPreferences } from '../../hooks/useGridPreferences'
import { useSavedViews } from '../../hooks/useSavedViews'
import { api } from '../../api/client'
import { applyCatalogueFilters, buildCatalogueFilterFields, FACET_LIST_CODE } from '../../catalogueOptions'
import { toSwissLocale, type SupportedLanguage } from '../../i18n'
import type {
  BrandPage,
  CatalogueBrowseMode,
  CatalogueFacetsRead,
  CatalogueModelGroupPage,
  CatalogueVariantPage,
  CatalogueVariantRead,
  ReferenceValuePage,
  ReferenceValueRead,
} from '../../api/types'

const GRID_KEY = 'mdm.catalogue.variants'
const DEFAULT_SORT: SortSpec[] = [{ field: 'variantName', direction: 'asc' }]

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
 * `(listCode, valueCode) -> label` lookup in the current UI language. */
function useReferenceLabels(): (listCode: string, valueCode: string) => string {
  const { i18n } = useTranslation()
  const lang = (i18n.language as SupportedLanguage) ?? 'de'
  const listCodes = useMemo(() => Array.from(new Set(Object.values(FACET_LIST_CODE))), [])

  const results = useQuery({
    queryKey: ['catalogue-facet-labels', listCodes],
    queryFn: async (): Promise<Record<string, ReferenceValuePage>> => {
      const empty: ReferenceValuePage = { items: [], nextCursor: null }
      const pages = await Promise.all(
        listCodes.map((code) =>
          api
            .get<ReferenceValuePage>(`/reference-data/${code}?limit=200`)
            .then((page) => [code, page] as const)
            .catch(() => [code, empty] as const),
        ),
      )
      return Object.fromEntries(pages)
    },
  })

  return (listCode: string, valueCode: string): string => {
    const page = results.data?.[listCode]
    const row = page?.items.find((v: ReferenceValueRead) => v.valueCode === valueCode)
    if (!row) return valueCode
    const key = `label${lang.charAt(0).toUpperCase()}${lang.slice(1)}` as 'labelDe' | 'labelFr' | 'labelIt' | 'labelEn'
    return row[key] || valueCode
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

/**
 * The catalogue results grid — drill-down scope, mirror-derived facets,
 * saved views, column config, selection. Standalone on `/catalogue`, and
 * embedded in the configurator overlay's "Find the car" phase (C-C).
 */
export function CatalogueBrowseGrid({ mode, onSelectVariant }: CatalogueBrowseGridProps) {
  const { t, i18n } = useTranslation()
  const locale = toSwissLocale(i18n.language as SupportedLanguage)
  const { density, setDensity } = useUiPreferencesContext()
  const gridPrefs = useGridPreferences(GRID_KEY, { sort: DEFAULT_SORT })
  const savedViews = useSavedViews(GRID_KEY)
  const [searchParams, setSearchParams] = useSearchParams()
  const labelFor = useReferenceLabels()

  const brandId = searchParams.get('brand') || null
  const modelGroupId = searchParams.get('modelGroup') || null
  const sort = searchParams.get('sort') ? parseSortParam(searchParams.get('sort')!) : gridPrefs.sort
  const predicates = searchParams.get('filters') ? parseFiltersParam(searchParams.get('filters')!) : []

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

  const brandsQuery = useQuery({
    queryKey: ['catalogue-brands'],
    queryFn: () => api.get<BrandPage>('/vehicle-mdm/brands?limit=200'),
  })
  const modelGroupsQuery = useQuery({
    queryKey: ['catalogue-model-groups', brandId],
    queryFn: () => api.get<CatalogueModelGroupPage>(`/catalogue/model-groups?brandId=${brandId}`),
    enabled: brandId !== null,
  })

  const facetScope = new URLSearchParams()
  if (brandId) facetScope.set('brandId', brandId)
  if (modelGroupId) facetScope.set('modelGroup', modelGroupId)
  facetScope.set('mode', mode)
  const facetsQuery = useQuery({
    queryKey: ['catalogue-facets', brandId, modelGroupId, mode],
    queryFn: () => api.get<CatalogueFacetsRead>(`/catalogue/facets?${facetScope.toString()}`),
  })

  const sortParam = sort.length > 0 ? serializeSort(sort) : undefined
  const variantsQuery = useInfiniteQuery({
    queryKey: ['catalogue-variants', brandId, modelGroupId, mode, debouncedQuery, sortParam, predicates],
    queryFn: async ({ pageParam }: { pageParam: string | null }) => {
      const params = new URLSearchParams()
      if (brandId) params.set('brandId', brandId)
      if (modelGroupId) params.set('modelGroup', modelGroupId)
      params.set('mode', mode)
      if (debouncedQuery) params.set('q', debouncedQuery)
      if (sortParam) params.set('sort', sortParam)
      applyCatalogueFilters(params, predicates)
      params.set('limit', '50')
      if (pageParam) params.set('cursor', pageParam)
      return api.get<CatalogueVariantPage>(`/catalogue/variants?${params.toString()}`)
    },
    initialPageParam: null as string | null,
    getNextPageParam: (lastPage) => lastPage.nextCursor,
  })

  const firstPage = variantsQuery.data?.pages[0]
  const browseAvailable = firstPage?.browseAvailable ?? true
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
        meta: { sortField: 'variantName', pinned: 'left', locked: true },
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
        cell: ({ row }) => row.original.spec.ps ?? '—',
        meta: { sortField: 'ps', align: 'right' },
      },
      {
        id: 'displacementCcm',
        header: t('catalogueBrowse.columns.displacementCcm'),
        cell: ({ row }) => row.original.spec.displacementCcm ?? '—',
        meta: { sortField: 'displacementCcm', align: 'right', defaultVisible: false },
      },
      {
        id: 'basePrice',
        header: t('catalogueBrowse.columns.basePrice'),
        cell: ({ row }) =>
          row.original.currentPrice ? formatChf(row.original.currentPrice.amount, locale) : '—',
        meta: { sortField: 'basePrice', align: 'right' },
      },
      {
        id: 'productionFrom',
        header: t('catalogueBrowse.columns.productionFrom'),
        cell: ({ row }) => row.original.modelYearFrom,
        meta: { sortField: 'modelYearFrom', align: 'right' },
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
        meta: { align: 'right', defaultVisible: false },
      },
      {
        id: 'seats',
        header: t('catalogueBrowse.columns.seats'),
        cell: ({ row }) => row.original.spec.seats ?? '—',
        meta: { align: 'right', defaultVisible: false },
      },
      {
        id: 'co2Gkm',
        header: t('catalogueBrowse.columns.co2Gkm'),
        cell: ({ row }) => row.original.spec.co2Gkm ?? '—',
        meta: { align: 'right', defaultVisible: false },
      },
    ],
    [t, locale, labelFor],
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

  const isFiltered = debouncedQuery.length > 0 || predicates.length > 0 || brandId !== null
  const currentViewName =
    (appliedViewId && savedViews.views.find((v) => v.id === appliedViewId)?.name) ||
    t('catalogueBrowse.allVariantsView')

  const applyView = (view: SavedView) => {
    setAppliedViewId(view.id)
    if (view.snapshot.sort) setSort(view.snapshot.sort)
    if (view.snapshot.columnLayout) gridPrefs.setColumnLayout(view.snapshot.columnLayout)
    if (view.snapshot.filters) setPredicates(view.snapshot.filters)
  }

  const brandOptions = (brandsQuery.data?.items ?? []).map((b) => ({ value: b.id, label: b.displayName }))
  const modelGroupOptions = (modelGroupsQuery.data?.items ?? []).map((g) => ({ value: g.id, label: g.name }))

  return (
    <Stack gap="md">
      <Group gap="sm" wrap="wrap">
        <Select
          aria-label={t('catalogueBrowse.drilldown.brand')}
          placeholder={t('catalogueBrowse.drilldown.brandPlaceholder')}
          data={brandOptions}
          value={brandId}
          onChange={(value) => updateUrl({ brand: value, modelGroup: null })}
          clearable
          searchable
          w={220}
        />
        <Select
          aria-label={t('catalogueBrowse.drilldown.modelGroup')}
          placeholder={
            brandId
              ? t('catalogueBrowse.drilldown.modelGroupPlaceholder')
              : t('catalogueBrowse.drilldown.modelGroupNeedsBrand')
          }
          data={modelGroupOptions}
          value={modelGroupId}
          onChange={(value) => updateUrl({ modelGroup: value })}
          disabled={!brandId}
          clearable
          searchable
          w={240}
        />
      </Group>

      {!browseAvailable && (
        <Alert icon={<Plug size={16} />} color="yellow" title={t('catalogueBrowse.unavailable.title')}>
          {t('catalogueBrowse.unavailable.body')}
        </Alert>
      )}

      <OverviewShellRegion
        header={<div />}
        actionBar={
          <>
            <ActionBar
              searchValue={query}
              onSearchChange={setQuery}
              searchPlaceholder={t('catalogueBrowse.searchPlaceholder')}
              density={density}
              onDensityChange={setDensity}
              onRefresh={() => variantsQuery.refetch()}
              refreshing={variantsQuery.isRefetching}
              filterSlot={
                <ViewsAndFilters
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
        <DataGrid<CatalogueVariantRead>
          columns={columns}
          rows={browseAvailable ? rows : []}
          getRowId={(row) => row.id}
          sort={sort}
          onSortChange={setSort}
          density={density}
          onRowActivate={onSelectVariant}
          loading={variantsQuery.isLoading}
          refetching={variantsQuery.isRefetching && !variantsQuery.isLoading}
          fetchingNextPage={variantsQuery.isFetchingNextPage}
          hasNextPage={Boolean(variantsQuery.hasNextPage)}
          onLoadMore={() => variantsQuery.fetchNextPage()}
          error={variantsQuery.isError ? t('catalogueBrowse.loadError') : null}
          onRetry={() => variantsQuery.refetch()}
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
            icon: <Database size={24} />,
            title: t('catalogueBrowse.emptyState.title'),
            description: t('catalogueBrowse.emptyState.description'),
          }}
          emptyFilteredState={{
            icon: <Database size={24} />,
            title: t('catalogueBrowse.emptyFilteredState.title'),
            description: t('catalogueBrowse.emptyFilteredState.description'),
          }}
        />
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

function formatChf(amount: string, locale: string): string {
  const n = Number(amount)
  return Number.isFinite(n) ? `CHF ${n.toLocaleString(locale)}` : amount
}
