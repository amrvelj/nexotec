import { useEffect, useMemo, useState } from 'react'
import { Link, useNavigate, useSearchParams } from 'react-router-dom'
import { Button, Group, Stack, Title } from '@mantine/core'
import { useDebouncedValue } from '@mantine/hooks'
import { useInfiniteQuery, useQueryClient } from '@tanstack/react-query'
import { Download, ExternalLink, Printer, Users } from 'lucide-react'
import { useTranslation } from 'react-i18next'
import {
  ActionBar,
  Badge,
  ColumnConfigPanel,
  CustomerTypeBadge,
  DataGrid,
  LanguageBadge,
  LifecycleStatusBadge,
  OverviewShellRegion,
  SelectionBar,
  ViewsAndFilters,
  defaultColumnLayout,
  deriveFilterFields,
  resolveColumnLayout,
  resolveRelativeDateRange,
  useSetBreadcrumb,
  type ColumnRegistryEntry,
  type DateCondition,
  type FilterPredicate,
  type GridColumnDef,
  type SavedView,
  type SortSpec,
} from '@nexotec/ui-kit'
import { useUiPreferencesContext } from '../hooks/UiPreferencesContext'
import { useGridPreferences } from '../hooks/useGridPreferences'
import { useSavedViews } from '../hooks/useSavedViews'
import { useCountryOptions } from '../hooks/useCountryOptions'
import { api } from '../api/client'
import { buildCustomerRowMenu } from '../components/customerRowMenu'
import { CreditBlockDialog } from '../components/customer-detail/CreditBlockDialog'
import { CustomerCreateDialog } from '../components/CustomerCreateDialog'
import { toSwissLocale, type SupportedLanguage } from '../i18n'
import {
  CANTON_OPTIONS,
  legalFormLabel,
  translatedCustomerTypeLabel,
  translatedCustomerTypeOptions,
  translatedLanguageOptions,
  translatedLifecycleLabel,
  translatedLifecycleOptions,
  translatedPreferredChannelLabel,
  translatedSalutationLabel,
  translatedSourceLabel,
} from '../customerOptions'
import { formatDate } from '../utils/format'
import { customerName } from '../utils/customer'
import { exportRowsToCsv, printRows, type ExportColumn } from '../utils/gridExport'
import type { CustomerPage, CustomerRead, SalesContractRead, SalesOfferRead } from '../api/types'

const GRID_KEY = 'mdm.customers.list'
const DEFAULT_SORT: SortSpec[] = [{ field: 'updatedAt', direction: 'desc' }]

// § ADR-056 — "grid state (search, filter, sort, tab, scope) lives in the
// URL, and the URL is the shareable unit. Layout and density stay out of
// it; those are the reader's own ergonomics, and belong on the user
// preference record." Column layout/density already come from
// useGridPreferences/useUiPreferencesContext, not this file's own state —
// only search/sort/filters are URL-synced below.

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

function serializeSort(sort: SortSpec[]): string {
  return sort.map((s) => `${s.field}:${s.direction}`).join(',')
}

function parseFiltersParam(raw: string): FilterPredicate[] {
  try {
    const parsed: unknown = JSON.parse(raw)
    return Array.isArray(parsed) ? (parsed as FilterPredicate[]) : []
  } catch {
    // A hand-edited or truncated URL shouldn't crash the screen — it just
    // opens with no filters, same as if none had ever been set.
    return []
  }
}

/**
 * `/customers` accepts a handful of fixed, independent query parameters —
 * no generic predicate engine exists server-side. `paramByFieldId` is
 * derived from the same column defs the filter builder is (`meta.filter`),
 * so there is no second hand-maintained map to drift (§ ADR-058).
 *
 * The two conditions the API cannot honour — a `select` field's "is not"
 * (the API takes one equality value per field) and a date's "more than N
 * days ago" (`updated_since` is a lower bound only; there is no "changed
 * before" parameter) — are not applied here because the columns that back
 * these fields do not offer them (`meta.filter.conditions`). The guards
 * below stay defensive regardless: an unknown field or condition is left
 * unsent, never misfiltered.
 */
function applyPredicatesToParams(
  params: URLSearchParams,
  predicates: FilterPredicate[],
  paramByFieldId: Map<string, string>,
) {
  for (const predicate of predicates) {
    const paramName = paramByFieldId.get(predicate.fieldId)
    if (!paramName) continue

    if (predicate.type === 'date') {
      if (predicate.condition === 'moreThanDaysAgo') continue
      const range = resolveRelativeDateRange(predicate.condition as DateCondition, predicate.days, new Date())
      if (range.from) params.set(paramName, range.from)
      continue
    }

    if (predicate.condition === 'is' && typeof predicate.value === 'string') {
      params.set(paramName, predicate.value)
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
  const [searchParams, setSearchParams] = useSearchParams()
  const { options: countryOptions } = useCountryOptions()

  // ISO 3166-1 alpha-2 -> localised country name, for the `nationality`
  // and `addressCountry` columns. Falls back to the raw code while the
  // reference list is still loading or for a code the list doesn't carry.
  const countryLabel = useMemo(() => {
    const byCode = new Map(countryOptions.map((o) => [o.value, o.label]))
    return (code: string | null | undefined): string => (code ? (byCode.get(code) ?? code) : '')
  }, [countryOptions])

  // "Pasting that URL reproduces the screen" — sort and filters are read
  // straight from the URL on every render (no local mirror to go stale
  // against a pasted/back-navigated URL); the preference is only the
  // fallback for "what to show when there's no URL state yet at all."
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
      { replace: true }
    )
  }

  const setSort = (next: SortSpec[]) => {
    gridPrefs.setSort(next) // persists as the future default too (U-01/U-09)
    updateUrl({ sort: next.length > 0 ? serializeSort(next) : null })
  }

  const setPredicates = (next: FilterPredicate[]) => {
    updateUrl({ filters: next.length > 0 ? JSON.stringify(next) : null })
  }

  // Search stays local while typing (immediate, no per-keystroke URL
  // churn) and is written to the URL debounced, alongside the fetch —
  // initialized from the URL so a pasted link still opens with the right
  // search text in the box.
  const [query, setQuery] = useState(() => searchParams.get('q') ?? '')
  const [debouncedQuery] = useDebouncedValue(query, 250)
  useEffect(() => {
    updateUrl({ q: debouncedQuery || null })
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [debouncedQuery])

  const [appliedViewId, setAppliedViewId] = useState<string | null>(null)
  const [selectedIds, setSelectedIds] = useState<Set<string>>(new Set())
  // KAN-44 — the credit-block set/clear form is reachable from the shared
  // row menu on this surface too (ADR-061), not only the detail screen.
  const [blockDialogCustomer, setBlockDialogCustomer] = useState<CustomerRead | null>(null)
  // FR-05 / FR-20 — creating a customer opens the shared dialog over the
  // list, not a navigation. `/customers/new` renders the same dialog for a
  // pasted link (App.tsx → CustomerCreatePage).
  const [createOpen, setCreateOpen] = useState(false)
  const queryClient = useQueryClient()

  const columns: GridColumnDef<CustomerRead>[] = useMemo(() => {
    // A stored string field: hidden by default, dash for empty on screen,
    // blank for empty in an export.
    const text = (
      id: string,
      header: string,
      pick: (row: CustomerRead) => string | null | undefined,
      opts: { mono?: boolean } = {},
    ): GridColumnDef<CustomerRead> => ({
      id,
      header,
      cell: ({ row }) => pick(row.original) || '—',
      meta: { defaultVisible: false, mono: opts.mono, exportValue: (row) => pick(row) ?? '' },
    })

    const date = (
      id: string,
      header: string,
      pick: (row: CustomerRead) => string | null | undefined,
    ): GridColumnDef<CustomerRead> => ({
      id,
      header,
      cell: ({ row }) => {
        const value = pick(row.original)
        return value ? formatDate(value, locale) : '—'
      },
      meta: {
        defaultVisible: false,
        align: 'right',
        exportValue: (row) => {
          const value = pick(row)
          return value ? formatDate(value, locale) : ''
        },
      },
    })

    return [
      {
        id: 'customerNumber',
        header: t('customersList.columns.customerNumber'),
        cell: ({ row }) => row.original.customerNumber,
        meta: {
          sortField: 'customerNumber',
          pinned: 'left',
          mono: true,
          locked: true,
          exportValue: (row) => row.customerNumber,
        },
      },
      {
        id: 'name',
        header: t('customersList.columns.name'),
        cell: ({ row }) => <span style={{ fontWeight: 600 }}>{customerName(row.original)}</span>,
        // § Composite cells (FR-17 as amended 2026-09-07) — name carries
        // locality as its secondary: inline at `default` density, stacked
        // at `comfortable`, gone at `compact`. The `contact` column below
        // is the other composite cell; both use `meta.secondary`, the one
        // mechanism DataGrid already implements.
        meta: {
          sortField: 'lastName',
          locked: true,
          secondary: (row) => row.address?.addressLocality ?? null,
          exportValue: (row) => customerName(row),
        },
      },
      {
        id: 'customerType',
        header: t('customersList.columns.type'),
        cell: ({ row }) => (
          <CustomerTypeBadge type={row.original.customerType} label={translatedCustomerTypeLabel(t, row.original.customerType)} />
        ),
        meta: {
          filter: { type: 'select', param: 'customer_type', options: translatedCustomerTypeOptions(t), conditions: ['is'] },
          exportValue: (row) => translatedCustomerTypeLabel(t, row.customerType),
        },
      },
      {
        // FR-17 as amended — the composite Contact cell: mobile primary,
        // email secondary, in ONE cell. When there is no mobile, email
        // takes the primary line and there is no secondary — no stray
        // separator. Both facts are ADR-067 read-model projections the
        // list endpoint already ships on every row.
        id: 'contact',
        header: t('customersList.columns.contact'),
        cell: ({ row }) => {
          const { phoneMobile, email } = row.original
          const primary = phoneMobile ?? email ?? '—'
          return (
            <span style={phoneMobile ? { fontFamily: 'ui-monospace, SF Mono, Menlo, monospace' } : undefined}>{primary}</span>
          )
        },
        meta: {
          secondary: (row) => (row.phoneMobile && row.email ? row.email : null),
          exportValue: (row) => [row.phoneMobile, row.email].filter(Boolean).join(' / '),
        },
      },
      {
        id: 'language',
        header: t('customersList.columns.language'),
        cell: ({ row }) => <LanguageBadge language={row.original.language} />,
        meta: {
          filter: { type: 'select', param: 'language', options: translatedLanguageOptions(t), conditions: ['is'] },
          exportValue: (row) => t(`customerEnums.language.${row.language}`),
        },
      },
      {
        id: 'lifecycleStatus',
        header: t('customersList.columns.status'),
        cell: ({ row }) => (
          <LifecycleStatusBadge status={row.original.lifecycleStatus} label={translatedLifecycleLabel(t, row.original.lifecycleStatus)} />
        ),
        meta: {
          filter: { type: 'select', param: 'lifecycle_status', options: translatedLifecycleOptions(t), conditions: ['is'] },
          exportValue: (row) => translatedLifecycleLabel(t, row.lifecycleStatus),
        },
      },
      // KAN-51 Half 2 (partial) — advisor and tags are already stored
      // (KAN-50) and already flow through CustomerRead on every row; only
      // vehicles / open deals / lifetime revenue / last contact (four of
      // the ratified 12 default cells) stay out. test_derived_fields_
      // are_not_writable_in_phase_b2 (tests/test_customer_fr17_fields.py)
      // keeps vehicle_count uncomputed alongside five other Phase-C
      // fields (purchase_count, lifetime_revenue, open_deals,
      // last_contact_at, service_due — only three of which are also grid
      // columns) — read as "these ship as one Relationship-region batch,
      // not staggered," so vehicles isn't cherry-picked out ahead of its
      // still-blocked grid siblings just because its own dependency
      // happens to be lighter.
      {
        id: 'advisor',
        header: t('customersList.columns.advisor'),
        cell: ({ row }) => row.original.advisorLabel ?? '—',
        meta: { exportValue: (row) => row.advisorLabel ?? '' },
      },
      {
        id: 'tags',
        header: t('customersList.columns.tags'),
        cell: ({ row }) => {
          const tags = row.original.tags ?? []
          return tags.length > 0 ? tags.join(', ') : '—'
        },
        meta: { exportValue: (row) => (row.tags ?? []).join(', ') },
      },

      // --- everything below: available (ADR-060 — every persisted field is
      // a grid column), hidden by default; the remaining Half 2 columns
      // (vehicles, open deals, lifetime revenue, last contact) land
      // together once the Phase-C reporting projection exists.
      text('salutation', t('customersList.columns.salutation'), (row) =>
        row.salutation ? translatedSalutationLabel(t, row.salutation) : null,
      ),
      text('firstName', t('customersList.columns.firstName'), (row) => row.firstName),
      text('lastName', t('customersList.columns.lastName'), (row) => row.lastName),
      text('companyName', t('customersList.columns.companyName'), (row) => row.companyName),
      text('legalForm', t('customersList.columns.legalForm'), (row) => (row.legalForm ? legalFormLabel(row.legalForm) : null)),
      date('birthDate', t('customersList.columns.birthDate'), (row) => row.birthDate),
      text('nationality', t('customersList.columns.nationality'), (row) => countryLabel(row.nationality)),

      text('phoneMobile', t('customersList.columns.phoneMobile'), (row) => row.phoneMobile, { mono: true }),
      text('phoneLandline', t('customersList.columns.phoneLandline'), (row) => row.phoneLandline, { mono: true }),
      text('phoneWork', t('customersList.columns.phoneWork'), (row) => row.phoneWork, { mono: true }),
      text('email', t('customersList.columns.email'), (row) => row.email),
      text('emailSecondary', t('customersList.columns.emailSecondary'), (row) => row.emailSecondary),

      text('addressStreet', t('customersList.columns.addressStreet'), (row) => row.address?.addressStreet),
      text('addressHouseNumber', t('customersList.columns.addressHouseNumber'), (row) => row.address?.addressHouseNumber),
      text('addressLine2', t('customersList.columns.addressLine2'), (row) => row.address?.addressLine2),
      text('addressPostalCode', t('customersList.columns.addressPostalCode'), (row) => row.address?.addressPostalCode, { mono: true }),
      text('addressLocality', t('customersList.columns.addressLocality'), (row) => row.address?.addressLocality),
      {
        id: 'addressCanton',
        header: t('customersList.columns.canton'),
        cell: ({ row }) => row.original.address?.addressCanton ?? '—',
        meta: {
          defaultVisible: false,
          filter: { type: 'select', param: 'canton', options: CANTON_OPTIONS, conditions: ['is'] },
          exportValue: (row) => row.address?.addressCanton ?? '',
        },
      },
      text('addressCountry', t('customersList.columns.country'), (row) => countryLabel(row.address?.addressCountry)),

      text('preferredChannel', t('customersList.columns.preferredChannel'), (row) =>
        row.preferredChannel ? translatedPreferredChannelLabel(t, row.preferredChannel) : null,
      ),
      text('source', t('customersList.columns.source'), (row) => (row.source ? translatedSourceLabel(t, row.source) : null)),
      text('sourceRef', t('customersList.columns.sourceRef'), (row) => row.sourceRef),
      {
        id: 'marketingConsent',
        header: t('customersList.columns.marketingConsent'),
        cell: ({ row }) => (row.original.marketingConsent ? '✓' : '—'),
        meta: {
          defaultVisible: false,
          align: 'right',
          exportValue: (row) => (row.marketingConsent ? t('common.yes') : t('common.no')),
        },
      },
      // KAN-44 / FR-18 — the credit block as a grid column, hidden by
      // default, badge-rendered with the reason as its title.
      {
        id: 'creditBlock',
        header: t('customersList.columns.creditBlock'),
        cell: ({ row }) =>
          row.original.creditBlock ? (
            <span title={row.original.creditBlockReason ?? undefined}>
              <Badge tone="destructive">{t('customersList.columns.creditBlock')}</Badge>
            </span>
          ) : (
            '—'
          ),
        meta: {
          defaultVisible: false,
          exportValue: (row) => (row.creditBlock ? (row.creditBlockReason ?? t('common.yes')) : ''),
        },
      },
      date('createdAt', t('customersList.columns.created'), (row) => row.createdAt),
      {
        id: 'updatedAt',
        header: t('customersList.columns.changed'),
        cell: ({ row }) => formatDate(row.original.updatedAt, locale),
        // Out of the default-visible set per the FR-17 §B amendment, but
        // still an available column and still the grid's default sort.
        meta: {
          defaultVisible: false,
          sortField: 'updatedAt',
          align: 'right',
          filter: {
            type: 'date',
            param: 'updated_since',
            conditions: ['today', 'thisWeek', 'thisMonth', 'thisYear', 'inTheLastDays'],
          },
          exportValue: (row) => formatDate(row.updatedAt, locale),
        },
      },
      text('duplicateOfCustomerId', t('customersList.columns.duplicateOf'), (row) => row.duplicateOfCustomerId, { mono: true }),
    ]
  }, [t, locale, countryLabel])

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

  // § ADR-058 — the filter field list IS the column registry: every column
  // that declares `meta.filter` is a filter field, in registry order.
  // Adding a filterable column is one edit (the column def), never a
  // second edit to a parallel list.
  const filterFields = useMemo(() => deriveFilterFields(columns), [columns])
  const filterParamByFieldId = useMemo(
    () => new Map(filterFields.map((f) => [f.id, f.param ?? f.id])),
    [filterFields]
  )

  // The effective layout: the user's saved one, or the module default
  // (which honours each column's `defaultVisible`). DataGrid,
  // ColumnConfigPanel and the export column set all read the SAME value,
  // so "what you see" is one definition — never the grid showing every
  // column just because no preference has been saved yet.
  const columnLayout = useMemo(
    () => gridPrefs.columnLayout ?? defaultColumnLayout(columnRegistry),
    [gridPrefs.columnLayout, columnRegistry]
  )

  const sortParam = sort.length > 0 ? sort.map((s) => `${s.field}:${s.direction}`).join(',') : undefined

  const { data, fetchNextPage, hasNextPage, isFetchingNextPage, isLoading, isError, refetch, isRefetching } =
    useInfiniteQuery({
      queryKey: ['customers', GRID_KEY, debouncedQuery, sortParam, predicates],
      queryFn: async ({ pageParam }: { pageParam: string | null }) => {
        const params = new URLSearchParams()
        if (debouncedQuery) params.set('q', debouncedQuery)
        if (sortParam) params.set('sort', sortParam)
        applyPredicatesToParams(params, predicates, filterParamByFieldId)
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

  // D-25 — Export and Print operate on the SELECTED rows in the CURRENTLY
  // VISIBLE columns, in their on-screen order. `visibleColumnIds` is the
  // same resolution DataGrid does for rendering, so "what you see is what
  // you get" is literally true.
  const visibleColumnIds = useMemo(
    () => resolveColumnLayout(columnRegistry, columnLayout).visibleOrder,
    [columnRegistry, columnLayout]
  )

  const exportColumns: ExportColumn<CustomerRead>[] = useMemo(() => {
    const byId = new Map(columns.map((c) => [String(c.id), c]))
    return visibleColumnIds
      .map((id) => byId.get(id))
      .filter((c): c is GridColumnDef<CustomerRead> => Boolean(c))
      .map((c) => ({
        header: typeof c.header === 'string' ? c.header : String(c.id),
        value: (row: CustomerRead) => {
          const raw = c.meta?.exportValue?.(row)
          return raw == null ? '' : String(raw)
        },
      }))
  }, [columns, visibleColumnIds])

  const selectedRows = useMemo(() => rows.filter((row) => selectedIds.has(row.id)), [rows, selectedIds])

  const exportSelection = () => {
    const stamp = new Date().toISOString().slice(0, 10)
    exportRowsToCsv(`customers_${stamp}.csv`, exportColumns, selectedRows)
  }
  const printSelection = () => {
    printRows(t('customersList.title'), exportColumns, selectedRows)
  }

  // Row-menu handlers this surface can do without a modal (link-vehicle
  // and merge stay on the detail screen — same posture ValuationsListPage
  // takes). "New offer" is the same POST-then-PATCH sequence
  // CustomerDetailPage runs, started from the row.
  const createOfferForCustomer = async (customerId: string) => {
    const created = await api.post<SalesOfferRead>('/sales/offers')
    const updated = await api.patch<SalesOfferRead>(
      `/sales/offers/${created.id}`,
      { customerId },
      { 'If-Match': String(created.version) },
    )
    navigate(`/sales/offers/${updated.id}`)
  }

  // KAN-58 — "New contract", the customer→contract entry point. Simpler
  // than the offer flow above: ContractCreate accepts customerId directly,
  // so this is one POST, not POST-then-PATCH.
  const createContractForCustomer = async (customerId: string) => {
    const created = await api.post<SalesContractRead>('/sales/contracts', { customerId })
    navigate(`/sales/contracts/${created.id}`)
  }

  const toggleDoNotContact = async (row: CustomerRead) => {
    await api.patch<CustomerRead>(
      `/customers/${row.id}`,
      { lifecycleStatus: row.lifecycleStatus === 'do_not_contact' ? 'active' : 'do_not_contact' },
      { 'If-Match': String(row.version) },
    )
    void queryClient.invalidateQueries({ queryKey: ['customers'] })
  }

  const isFiltered = debouncedQuery.length > 0 || predicates.length > 0
  const currentViewName = (appliedViewId && savedViews.views.find((v) => v.id === appliedViewId)?.name) || t('customersList.allCustomersView')

  const applyView = (view: SavedView) => {
    setAppliedViewId(view.id)
    if (view.snapshot.sort) setSort(view.snapshot.sort)
    if (view.snapshot.columnLayout) gridPrefs.setColumnLayout(view.snapshot.columnLayout)
    if (view.snapshot.filters) setPredicates(view.snapshot.filters)
  }

  return (
    <Stack gap="md">
      <Group justify="space-between">
        <Title order={2}>{t('customersList.title')}</Title>
        <Button onClick={() => setCreateOpen(true)}>{t('customersList.newCustomer')}</Button>
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
              columnsSlot={
                <ColumnConfigPanel
                  registry={columnRegistry}
                  layout={columnLayout}
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

            {selectedIds.size > 0 ? (
              <SelectionBar
                count={selectedIds.size}
                onClear={() => setSelectedIds(new Set())}
                countLabel={(n) => t('customersList.selection.count', { count: n })}
                clearLabel={t('customersList.selection.clear')}
                actions={[
                  {
                    // D-25 — the SELECTED rows in the CURRENTLY VISIBLE
                    // columns, as a real CSV file. Not "copy IDs".
                    label: t('customersList.selection.export'),
                    icon: <Download size={14} />,
                    onClick: exportSelection,
                  },
                  {
                    label: t('customersList.selection.print'),
                    icon: <Printer size={14} />,
                    onClick: printSelection,
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
          onSortChange={setSort}
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
          columnLayout={columnLayout}
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
            action: <Button onClick={() => setCreateOpen(true)}>{t('customersList.newCustomer')}</Button>,
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
          rowActions={(row) => {
            // KAN-44 / ADR-061 — the SAME builder the detail screen's
            // header/overflow uses, so "New offer" / "New contract" can't
            // drift between the two surfaces. Link-vehicle and merge are
            // modal-heavy and stay on the detail screen.
            const menu = buildCustomerRowMenu(t, row, {
              onEdit: () => navigate(`/customers/${row.id}`),
              onNewOffer: () => void createOfferForCustomer(row.id),
              onNewContract: () => void createContractForCustomer(row.id),
              onCopyCustomerNumber: () => void navigator.clipboard.writeText(row.customerNumber),
              onToggleDoNotContact: () => void toggleDoNotContact(row),
              onManageCreditBlock: () => setBlockDialogCustomer(row),
            })
            return {
              navigate: [
                {
                  label: t('customersList.rowActions.open'),
                  icon: <ExternalLink size={16} />,
                  onClick: () => navigate(`/customers/${row.id}`),
                },
              ],
              ...menu.overflow,
            }
          }}
        />
      </OverviewShellRegion>

      {blockDialogCustomer && (
        <CreditBlockDialog
          opened
          onClose={() => setBlockDialogCustomer(null)}
          customer={blockDialogCustomer}
          onSaved={() => {
            setBlockDialogCustomer(null)
            void queryClient.invalidateQueries({ queryKey: ['customers'] })
          }}
        />
      )}

      <CustomerCreateDialog
        opened={createOpen}
        onClose={() => setCreateOpen(false)}
        onCreated={(customer) => {
          setCreateOpen(false)
          void queryClient.invalidateQueries({ queryKey: ['customers'] })
          navigate(`/customers/${customer.id}`)
        }}
        onOpenExisting={(customerId) => navigate(`/customers/${customerId}`)}
      />
    </Stack>
  )
}
