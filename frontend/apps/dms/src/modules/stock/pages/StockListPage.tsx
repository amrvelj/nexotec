import { useEffect, useMemo, useState } from 'react'
import { Link, useNavigate, useSearchParams } from 'react-router-dom'
import { Anchor, Button, Group, Stack, Title } from '@mantine/core'
import { useDebouncedValue } from '@mantine/hooks'
import { useInfiniteQuery, useQuery } from '@tanstack/react-query'
import { ExternalLink, Warehouse } from 'lucide-react'
import { useTranslation } from 'react-i18next'
import {
  ActionBar,
  ColumnConfigPanel,
  type ColumnRegistryEntry,
  DataGrid,
  defaultColumnLayout,
  type FilterPredicate,
  type GridColumnDef,
  offeredPredicates,
  OverviewShellRegion,
  type SavedView,
  semantic,
  type SortSpec,
  sortSpecFromQueryParam,
  sortSpecToQueryParam,
  StockConditionBadge,
  StockLifecycleBadge,
  StockReservationBadge,
  useSetBreadcrumb,
  ViewsAndFilters,
} from '@nexotec/ui-kit'
import { useUiPreferencesContext } from '../../../hooks/UiPreferencesContext'
import { useGridPreferences } from '../../../hooks/useGridPreferences'
import { useColumnPanelLabels } from '../../../hooks/useColumnPanelLabels'
import { useViewsAndFiltersLabels } from '../../../hooks/useViewsAndFiltersLabels'
import { useSavedViews } from '../../../hooks/useSavedViews'
import { api } from '../../../api/client'
import { toSwissLocale, type SupportedLanguage } from '../../../i18n'
import {
  applyStockFilters,
  buildStockFilterFields,
  translatedStockConditionLabel,
  translatedStockLifecycleLabel,
  translatedStockReservationLabel,
} from '../stockOptions'
import { formatCurrencyChf, formatDate, formatDateOnly, formatNumber } from '../../../utils/format'
import { GroupStockGrid } from '../components/GroupStockGrid'
import { buildStockGroupColumns } from '../columns/stockGroupColumns'
import { STOCK_VEHICLE_COLUMN_WIDTH } from '../columns/stockVehicleColumn'
import { buildStockRowMenu, stockOpenState, type StockOpenAction } from '../components/stockRowMenu'
import { AddToPipelineButton } from '../components/AddToPipelineButton'
import { useCanWrite } from '../../../auth/useCanWrite'
import { ScopeSwitchMenu, type StockScope } from '../components/ScopeSwitchMenu'
import type { GridSchemaRead, StockItemPage, StockItemRead } from '../../../api/types'
import { sortableFieldsOf } from '../../../shared/gridSchema'
import { useActionBarLabels } from '../../../hooks/useActionBarLabels'
import { useDataGridLabels } from '../../../hooks/useDataGridLabels'

const GRID_KEY = 'inventory.stock.list'
const DEFAULT_SORT: SortSpec[] = [{ field: 'updatedAt', direction: 'desc' }]

export function StockListPage() {
  const { t, i18n } = useTranslation()
  const dataGridLabels = useDataGridLabels()
  const locale = toSwissLocale(i18n.language as SupportedLanguage)
  useSetBreadcrumb([t('shell.nav.inventory')])
  const navigate = useNavigate()
  const { density, setDensity } = useUiPreferencesContext()
  const gridPrefs = useGridPreferences(GRID_KEY, { sort: DEFAULT_SORT })
  const savedViews = useSavedViews(GRID_KEY)
  const [searchParams, setSearchParams] = useSearchParams()
  // § ADR-056 — search, filters, sort AND scope live in the URL, so a group-
  // scope list can be bookmarked, shared and reloaded (KAN-152). Own scope
  // is the default and leaves no parameter.
  const scope: StockScope = searchParams.get('scope') === 'group' ? 'group' : 'own'

  // KAN-461: the endpoint's served column registry — which fields filter,
  // under which conditions, which headers sort, which columns show by
  // default. Fetched once; the list waits for it, so a link's filters are
  // neither dropped nor sent unchecked meanwhile.
  const { data: gridSchema } = useQuery({
    queryKey: ['grid-schema', 'stock-items'],
    queryFn: () => api.get<GridSchemaRead>('/inventory/stock-items/grid-schema'),
    staleTime: Infinity,
  })
  const sortableFields = useMemo(() => sortableFieldsOf(gridSchema), [gridSchema])

  const sort = searchParams.get('sort') ? sortSpecFromQueryParam(searchParams.get('sort')) : gridPrefs.sort
  // KAN-458: a predicate from the URL or a saved view that this grid does
  // not offer is neither sent nor shown as an active filter.
  const filterFields = useMemo(() => buildStockFilterFields(t, gridSchema), [t, gridSchema])
  const storedFilters = useMemo(
    () => (searchParams.get('filters') ? safeParseFilters(searchParams.get('filters')!) : []),
    [searchParams]
  )
  const predicates = offeredPredicates(storedFilters, filterFields)

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
    updateUrl({ sort: sortSpecToQueryParam(next) ?? null })
  }

  const [appliedViewId, setAppliedViewId] = useState<string | null>(null)

  const setScope = (next: StockScope) => {
    // The two grids filter on different fields (the group grid on the
    // Betrieb alone, § ADR-055, KAN-565), so a scope switch drops the
    // filters either way: a shared URL must never claim a filter the grid it
    // opens does not apply (§ ADR-058).
    updateUrl(next === 'group' ? { scope: 'group', filters: null } : { scope: null, filters: null })
    // A saved view is a set of filters (§ ADR-058); with its filters gone,
    // the control must stop naming it.
    setAppliedViewId(null)
  }

  // The group grid sorts on its own short list (the backend's
  // GROUP_STOCK_SORT_FIELDS: no private value orders a sister's rows,
  // ADR-055); its sort lives in the URL alone (its column layout is persisted
  // under its own grid key, GroupStockGrid, KAN-357). A sort it cannot apply —
  // the own grid's price, carried over by a scope switch or a link — is
  // narrowed away, never sent (KAN-461).
  const urlGroupSort = searchParams.get('sort') ? sortSpecFromQueryParam(searchParams.get('sort')) : []
  // The fields the group grid's own headers sort (stockGroupColumns; the
  // backend's list is held to them by test_inventory_group_listing_api.py).
  const groupSortFields = useMemo(
    () => new Set(buildStockGroupColumns(t, locale).flatMap((c) => (c.meta?.sortField ? [c.meta.sortField] : []))),
    [t, locale]
  )
  const offeredGroupSort = urlGroupSort.filter((s) => groupSortFields.has(s.field))
  const groupSort = offeredGroupSort.length > 0 ? offeredGroupSort : DEFAULT_SORT
  const setGroupSort = (next: SortSpec[]) => {
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

  const sortParam = sortSpecToQueryParam(sort)

  const { data, fetchNextPage, hasNextPage, isFetchingNextPage, isLoading, isError, refetch, isRefetching } =
    useInfiniteQuery({
      queryKey: ['stock-items', GRID_KEY, debouncedQuery, sortParam, predicates],
      queryFn: async ({ pageParam }: { pageParam: string | null }) => {
        const params = new URLSearchParams()
        if (debouncedQuery) params.set('q', debouncedQuery)
        if (sortParam) params.set('sort', sortParam)
        applyStockFilters(params, predicates, filterFields)
        params.set('limit', '50')
        if (pageParam) params.set('cursor', pageParam)
        return api.get<StockItemPage>(`/inventory/stock-items?${params.toString()}`)
      },
      initialPageParam: null as string | null,
      getNextPageParam: (lastPage) => lastPage.nextCursor,
      enabled: scope === 'own' && gridSchema !== undefined,
    })

  const rows = useMemo(() => data?.pages.flatMap((page) => page.items) ?? [], [data])
  const total = data?.pages[0]?.total ?? null
  const totalIsEstimate = data?.pages[0]?.totalIsEstimate ?? false


  // § ADR-060 — every persisted field is a column (KAN-461: the served
  // registry, app/inventory/services/stock_grid.py). Which ones show by
  // default and which sort come from the schema; this page says only how a
  // value is drawn.
  const columns: GridColumnDef<StockItemRead>[] = useMemo(() => {
    const yesNo = (value: boolean | null | undefined) => (value == null ? '—' : value ? t('common.yes') : t('common.no'))
    return [
      {
        id: 'stockNumber',
        header: t('stockList.columns.stockNumber'),
        cell: ({ row }) => row.original.stockNumber,
        meta: { pinned: 'left', mono: true, locked: true },
      },
      {
        id: 'vehicleLabel',
        header: t('stockList.columns.vehicle'),
        cell: ({ row }) => <span style={{ fontWeight: 600 }}>{row.original.vehicleLabel}</span>,
        meta: { locked: true, width: STOCK_VEHICLE_COLUMN_WIDTH, secondary: (row) => row.vin },
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
        cell: ({ row }) => (row.original.odometerKm != null ? formatNumber(row.original.odometerKm) : '—'),
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
        meta: { align: 'right' },
      },
      // KAN-739: the location's name (rule 2's copy beside locationId).
      {
        id: 'locationLabel',
        header: t('stockList.columns.location'),
        cell: ({ row }) => row.original.locationLabel ?? '—',
      },
      // ADR-060: every persisted field is a column — VIN hidden by default
      // (already ride-alongs as vehicleLabel's secondary line above).
      {
        id: 'vin',
        header: t('stockList.columns.vin'),
        cell: ({ row }) => row.original.vin ?? '—',
        meta: { mono: true },
      },
      {
        id: 'listPrice',
        header: t('stockList.columns.listPrice'),
        cell: ({ row }) => (row.original.listPrice != null ? formatCurrencyChf(Number(row.original.listPrice)) : '—'),
        meta: { align: 'right' },
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
      },
      {
        id: 'basePrice',
        header: t('stockList.columns.basePrice'),
        cell: ({ row }) => (row.original.basePrice != null ? formatCurrencyChf(Number(row.original.basePrice)) : '—'),
        meta: { align: 'right' },
      },
      {
        id: 'purchasePrice',
        header: t('stockList.columns.purchasePrice'),
        cell: ({ row }) => (row.original.purchasePrice != null ? formatCurrencyChf(Number(row.original.purchasePrice)) : '—'),
        meta: { align: 'right' },
      },
      {
        id: 'landedCost',
        header: t('stockList.columns.landedCost'),
        cell: ({ row }) => (row.original.landedCost != null ? formatCurrencyChf(Number(row.original.landedCost)) : '—'),
        meta: { align: 'right' },
      },
      {
        id: 'notionalInputTaxAmount',
        header: t('stockList.columns.notionalInputTaxAmount'),
        cell: ({ row }) => (row.original.notionalInputTaxAmount != null ? formatCurrencyChf(Number(row.original.notionalInputTaxAmount)) : '—'),
        meta: { align: 'right' },
      },
      {
        id: 'isInvoiceable',
        header: t('stockList.columns.isInvoiceable'),
        cell: ({ row }) => yesNo(row.original.isInvoiceable),
      },
      {
        id: 'supplierLabel',
        header: t('stockList.columns.supplierName'),
        // KAN-465: the supplier contact's name, linking to the contact; a booking from before keeps its typed name.
        cell: ({ row }) =>
          row.original.supplierContactId ? (
            <Anchor
              component={Link}
              to={`/contacts/${row.original.supplierContactId}`}
              fz="sm"
              onClick={(event) => event.stopPropagation()}
            >
              {row.original.supplierLabel ?? '—'}
            </Anchor>
          ) : (
            (row.original.supplierLabel ?? '—')
          ),
      },
      {
        id: 'purchaseDate',
        header: t('stockList.columns.purchaseDate'),
        cell: ({ row }) => (row.original.purchaseDate ? formatDateOnly(row.original.purchaseDate, locale) : '—'),
      },
      {
        id: 'orderDate',
        header: t('stockList.columns.orderDate'),
        cell: ({ row }) => (row.original.orderDate ? formatDateOnly(row.original.orderDate, locale) : '—'),
      },
      {
        id: 'expectedDelivery',
        header: t('stockList.columns.expectedDelivery'),
        cell: ({ row }) => (row.original.expectedDelivery ? formatDateOnly(row.original.expectedDelivery, locale) : '—'),
      },
      {
        id: 'pipelineRef',
        header: t('stockList.columns.pipelineRef'),
        cell: ({ row }) => row.original.pipelineRef ?? '—',
        meta: { mono: true },
      },
      // KAN-190 (ADR-060): the configuration's label, rule 2's copy on the item.
      {
        id: 'configurationLabel',
        header: t('stockList.columns.configuration'),
        cell: ({ row }) => row.original.configurationLabel ?? '—',
      },
      // KAN-461: the persisted fields the grid did not offer before.
      {
        id: 'exteriorColour',
        header: t('stockList.columns.exteriorColour'),
        cell: ({ row }) => row.original.exteriorColour ?? '—',
      },
      {
        id: 'bodyStyle',
        header: t('stockList.columns.bodyStyle'),
        cell: ({ row }) => row.original.bodyStyle ?? '—',
      },
      {
        id: 'firstRegistrationDate',
        header: t('stockList.columns.firstRegistrationDate'),
        cell: ({ row }) => (row.original.firstRegistrationDate ? formatDateOnly(row.original.firstRegistrationDate, locale) : '—'),
      },
      {
        id: 'inStockAt',
        header: t('stockList.columns.inStockAt'),
        cell: ({ row }) => (row.original.inStockAt ? formatDate(row.original.inStockAt, locale) : '—'),
        meta: { align: 'right' },
      },
      {
        id: 'tradeInVin',
        header: t('stockList.columns.tradeInVin'),
        cell: ({ row }) => row.original.tradeInVin ?? '—',
        meta: { mono: true },
      },
      {
        id: 'tradeInSignedAt',
        header: t('stockList.columns.tradeInSignedAt'),
        cell: ({ row }) => (row.original.tradeInSignedAt ? formatDate(row.original.tradeInSignedAt, locale) : '—'),
        meta: { align: 'right' },
      },
      {
        id: 'supplierIsVatRegistered',
        header: t('stockList.columns.supplierIsVatRegistered'),
        cell: ({ row }) => yesNo(row.original.supplierIsVatRegistered),
      },
      {
        id: 'purchaseInvoiceRef',
        header: t('stockList.columns.purchaseInvoiceRef'),
        cell: ({ row }) => row.original.purchaseInvoiceRef ?? '—',
        meta: { mono: true },
      },
      {
        id: 'notionalInputTaxApplicable',
        header: t('stockList.columns.notionalInputTaxApplicable'),
        cell: ({ row }) => yesNo(row.original.notionalInputTaxApplicable),
      },
      {
        id: 'notionalInputTaxRate',
        header: t('stockList.columns.notionalInputTaxRate'),
        cell: ({ row }) =>
          row.original.notionalInputTaxRate != null ? `${formatNumber(Number(row.original.notionalInputTaxRate), 2)} %` : '—',
        meta: { align: 'right' },
      },
      {
        id: 'notionalInputTaxOverridden',
        header: t('stockList.columns.notionalInputTaxOverridden'),
        cell: ({ row }) => yesNo(row.original.notionalInputTaxOverridden),
      },
      {
        id: 'valuationRefAmount',
        header: t('stockList.columns.valuationRefAmount'),
        cell: ({ row }) => (row.original.valuationRefAmount != null ? formatCurrencyChf(Number(row.original.valuationRefAmount)) : '—'),
        meta: { align: 'right' },
      },
      {
        id: 'valuationRefValuedAt',
        header: t('stockList.columns.valuationRefValuedAt'),
        cell: ({ row }) => (row.original.valuationRefValuedAt ? formatDate(row.original.valuationRefValuedAt, locale) : '—'),
        meta: { align: 'right' },
      },
      {
        id: 'valuationRefSource',
        header: t('stockList.columns.valuationRefSource'),
        cell: ({ row }) => row.original.valuationRefSource ?? '—',
      },
      {
        id: 'createdAt',
        header: t('stockList.columns.createdAt'),
        cell: ({ row }) => formatDate(row.original.createdAt, locale),
        meta: { align: 'right' },
      },
    ]
  }, [t, locale])

  // KAN-461: default visibility is the served registry's — a column the
  // schema does not show by default is one click away.
  const defaultVisible = useMemo(
    () => new Map((gridSchema?.fields ?? []).map((f) => [f.field, f.defaultVisible])),
    [gridSchema]
  )

  const columnRegistry: ColumnRegistryEntry[] = useMemo(
    () =>
      columns.map((c) => ({
        id: String(c.id),
        label: typeof c.header === 'string' ? c.header : String(c.id),
        // The served schema decides (KAN-461); a column it does not know is hidden.
        defaultVisible: defaultVisible.get(String(c.id)) ?? false,
        locked: c.meta?.locked,
      })),
    [columns, defaultVisible]
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

  const isFiltered = debouncedQuery.length > 0 || predicates.length > 0
  const currentViewName = (appliedViewId && savedViews.views.find((v) => v.id === appliedViewId)?.name) || t('stockList.allStockView')

  // KAN-430: the intake writes a configuration and a stock item; a role
  // that may write neither is not offered it.
  const canWriteStockItems = useCanWrite('stock_items')
  const canWriteConfigurations = useCanWrite('configurations')
  const canBookPurchase = useCanWrite('stock_acquisition')
  const canChangeCondition = useCanWrite('stock_condition')
  const canCreateContract = useCanWrite('sales_contracts')
  const canCreateOffer = useCanWrite('sales_offers')
  const canCorrectVin = useCanWrite('stock_vin_correction')
  const canAddVehicle = canWriteStockItems && canWriteConfigurations

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
          {canAddVehicle && <AddToPipelineButton />}
        </Group>
      </Group>

      {scope === 'group' ? (
        <GroupStockGrid
          query={query}
          debouncedQuery={debouncedQuery}
          onQueryChange={setQuery}
          sort={groupSort}
          onSortChange={setGroupSort}
          filters={storedFilters}
          onFiltersChange={setPredicates}
        />
      ) : (
      <OverviewShellRegion
        header={<div />}
        actionBar={
          <ActionBar
            labels={actionBarLabels}
            searchValue={query}
            onSearchChange={setQuery}
            searchPlaceholder={t('stockList.searchPlaceholder')}
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
        <DataGrid<StockItemRead>
          columns={columns}
          rows={rows}
          getRowId={(row) => row.id}
          sort={sort}
          onSortChange={setSort}
          sortableFields={sortableFields}
          density={density}
          rowHref={(row) => `/stock/${row.id}`}
          linkComponent={Link}
          // KAN-461: the list waits for the schema; until then it is loading,
          // never «no cars».
          loading={isLoading || gridSchema === undefined}
          refetching={isRefetching && !isLoading}
          fetchingNextPage={isFetchingNextPage}
          hasNextPage={Boolean(hasNextPage)}
          onLoadMore={() => fetchNextPage()}
          error={isError ? t('stockList.loadError') : null}
          onRetry={() => refetch()}
          total={total}
          totalIsEstimate={totalIsEstimate}
          isFiltered={isFiltered}
          columnLayout={columnLayout}
          onColumnLayoutChange={gridPrefs.setColumnLayout}
          labels={dataGridLabels}
          emptyState={{
            icon: <Warehouse size={24} />,
            title: t('stockList.emptyState.title'),
            // KAN-511: a reader is not told to add a car it is not offered.
            description: t(canAddVehicle ? 'stockList.emptyState.description' : 'stockList.emptyState.descriptionReadOnly'),
            // KAN-463: the same intake as the header — the configurator.
            action: canAddVehicle ? <AddToPipelineButton /> : undefined,
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
          rowActions={(row) => {
            // KAN-571 / ADR-061 — the SAME builder the stock detail's header
            // uses, so list and detail cannot drift apart. Every action needs
            // the detail's own state (its dialogs, the configurator, the deal
            // it starts), so the car opens right there at the action picked.
            const openAt = (action: StockOpenAction) => () =>
              navigate(`/stock/${row.id}`, { state: stockOpenState(action) })
            const menu = buildStockRowMenu(
              t,
              row,
              {
                createContract: openAt('createContract'),
                createOffer: openAt('createOffer'),
                recordPurchase: openAt('recordPurchase'),
                stornoPurchase: openAt('stornoPurchase'),
                changeCondition: openAt('changeCondition'),
                attachConfiguration: openAt('attachConfiguration'),
                editConfiguration: openAt('editConfiguration'),
                correctVin: openAt('correctVin'),
              },
              {
                canCreateContract,
                canCreateOffer,
                canBookPurchase,
                canChangeCondition,
                canAttachConfiguration: canWriteStockItems && canWriteConfigurations,
                canEditConfiguration: canWriteConfigurations,
                canCorrectVin,
              },
              row.leftStockAt ? formatDate(row.leftStockAt, locale) : null,
            )
            return {
              navigate: [
                {
                  label: t('stockList.rowActions.open'),
                  icon: <ExternalLink size={16} />,
                  onClick: () => navigate(`/stock/${row.id}`),
                },
              ],
              ...menu.overflow,
            }
          }}
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
