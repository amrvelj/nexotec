import { useEffect, useMemo, useState } from 'react'
import { Link, useNavigate, useSearchParams } from 'react-router-dom'
import { Alert, Button, Group, Stack, Title } from '@mantine/core'
import { useDebouncedValue } from '@mantine/hooks'
import { useInfiniteQuery, useQueryClient } from '@tanstack/react-query'
import { Download, ExternalLink, Printer, Users } from 'lucide-react'
import { useTranslation } from 'react-i18next'
import {
  ActionBar,
  Badge,
  ColumnConfigPanel,
  type ColumnRegistryEntry,
  ContactTypeBadge,
  DataGrid,
  defaultColumnLayout,
  deriveFilterFields,
  type FilterPredicate,
  type GridColumnDef,
  LanguageBadge,
  LifecycleStatusBadge,
  offeredPredicates,
  OverviewShellRegion,
  resolveColumnLayout,
  type SavedView,
  SelectionBar,
  type SortSpec,
  sortSpecFromQueryParam,
  sortSpecToQueryParam,
  useSetBreadcrumb,
  ViewsAndFilters,
} from '@nexotec/ui-kit'
import { useUiPreferencesContext } from '../../../hooks/UiPreferencesContext'
import { useGridPreferences } from '../../../hooks/useGridPreferences'
import { useColumnPanelLabels } from '../../../hooks/useColumnPanelLabels'
import { useViewsAndFiltersLabels } from '../../../hooks/useViewsAndFiltersLabels'
import { useIdempotencyKey } from '../../../hooks/useIdempotencyKey'
import { useSavedViews } from '../../../hooks/useSavedViews'
import { useCountryOptions } from '../../../hooks/useCountryOptions'
import { useAdvisorOptions } from '../../../hooks/useAdvisorOptions'
import { useContactGroups } from '../../../hooks/useContactGroups'
import { api, ApiError } from '../../../api/client'
import { useAuth } from '../../../auth/AuthContext'
import { useCanWrite } from '../../../auth/useCanWrite'
import { buildContactRowMenu } from '../components/contactRowMenu'
import { CreditBlockDialog } from '../components/contact-detail/CreditBlockDialog'
import { ContactCreateDialog } from '../components/ContactCreateDialog'
import { toSwissLocale, type SupportedLanguage } from '../../../i18n'
import { languageEndonym } from '../../../i18n/languageEndonyms'
import { CANTON_OPTIONS } from '../../../shared/cantons'
import {
  UNASSIGNED_ADVISOR,
  applyContactFilters,
  translatedContactTypeLabel,
  translatedContactTypeOptions,
  translatedGenderLabel,
  LANGUAGE_OPTIONS,
  translatedLifecycleLabel,
  showsLifecycleStatus,
  translatedRoleLabel,
  translatedRoleOptions,
  translatedLifecycleOptions,
  translatedPaymentTermsLabel,
  translatedPreferredChannelLabel,
  translatedSalutationLabel,
  translatedSourceLabel,
  translatedLegalFormLabel,
} from '../contactOptions'
import { formatCurrencyChf, formatDate, formatDateOnly, todayLocalIsoDate } from '../../../utils/format'
import { contactName } from '../../../shared/displayNames'
import { exportRowsToCsv, printRows, type ExportColumn } from '../../../utils/gridExport'
import type { ContactPage, ContactRead, SalesContractRead, SalesOfferRead } from '../../../api/types'
import { useActionBarLabels } from '../../../hooks/useActionBarLabels'
import { apiRefusalText } from '../../../utils/apiRefusal'
import { useDataGridLabels } from '../../../hooks/useDataGridLabels'

const GRID_KEY = 'mdm.contacts.list'
const DEFAULT_SORT: SortSpec[] = [{ field: 'updatedAt', direction: 'desc' }]

// § ADR-056 — "grid state (search, filter, sort, tab, scope) lives in the
// URL, and the URL is the shareable unit. Layout and density stay out of
// it; those are the reader's own ergonomics, and belong on the user
// preference record." Column layout/density already come from
// useGridPreferences/useUiPreferencesContext, not this file's own state —
// only search/sort/filters are URL-synced below.

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

export function ContactsListPage() {
  const { t, i18n } = useTranslation()
  const dataGridLabels = useDataGridLabels()
  const locale = toSwissLocale(i18n.language as SupportedLanguage)
  // KAN-464 Q3: whether the row menu offers the customer role's actions.
  const mayWriteCustomerRole = useCanWrite('contact_customer_role')
  useSetBreadcrumb([t('shell.nav.masterData'), t('shell.nav.contacts')])
  const navigate = useNavigate()
  // KAN-377 (D-A-04): "New offer" / "New contract" only for a Betrieb that
  // holds the sales module — a workshop's API answers 404 on `/sales/*`.
  const { entitlements } = useAuth()
  const salesEntitled = entitlements.includes('sales')
  const { density, setDensity } = useUiPreferencesContext()
  const gridPrefs = useGridPreferences(GRID_KEY, { sort: DEFAULT_SORT })
  const savedViews = useSavedViews(GRID_KEY)
  const [searchParams, setSearchParams] = useSearchParams()
  const { options: countryOptions } = useCountryOptions()
  const { options: advisorOptions } = useAdvisorOptions()
  const contactGroups = useContactGroups()
  const contactGroupOptions = useMemo(
    () => contactGroups.items.map((item) => ({ value: item.valueCode, label: contactGroups.label(item.valueCode) })),
    [contactGroups],
  )

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
  const sort = searchParams.get('sort') ? sortSpecFromQueryParam(searchParams.get('sort')) : gridPrefs.sort
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
      { replace: true }
    )
  }

  const setSort = (next: SortSpec[]) => {
    gridPrefs.setSort(next) // persists as the future default too (U-01/U-09)
    updateUrl({ sort: sortSpecToQueryParam(next) ?? null })
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
  const [blockDialogContact, setBlockDialogContact] = useState<ContactRead | null>(null)
  // FR-05 / FR-20 — creating a contact opens the shared dialog over the
  // list, not a navigation. `/contacts/new` renders the same dialog for a
  // pasted link (App.tsx → ContactCreatePage).
  const [createOpen, setCreateOpen] = useState(false)
  const [actionError, setActionError] = useState<string | null>(null)
  const queryClient = useQueryClient()
  const offerCreateKey = useIdempotencyKey()
  const contractCreateKey = useIdempotencyKey()

  const columns: GridColumnDef<ContactRead>[] = useMemo(() => {
    // A stored string field: hidden by default, dash for empty on screen,
    // blank for empty in an export.
    const text = (
      id: string,
      header: string,
      pick: (row: ContactRead) => string | null | undefined,
      opts: { mono?: boolean } = {},
    ): GridColumnDef<ContactRead> => ({
      id,
      header,
      cell: ({ row }) => pick(row.original) || '—',
      meta: { defaultVisible: false, mono: opts.mono, exportValue: (row) => pick(row) ?? '' },
    })

    const date = (
      id: string,
      header: string,
      pick: (row: ContactRead) => string | null | undefined,
      // KAN-333: a date-only field names a calendar day and renders through
      // formatDateOnly; timestamps keep formatDate.
      format: (value: string, locale: string) => string = formatDate,
    ): GridColumnDef<ContactRead> => ({
      id,
      header,
      cell: ({ row }) => {
        const value = pick(row.original)
        return value ? format(value, locale) : '—'
      },
      meta: {
        defaultVisible: false,
        align: 'right',
        exportValue: (row) => {
          const value = pick(row)
          return value ? format(value, locale) : ''
        },
      },
    })

    return [
      {
        id: 'contactNumber',
        header: t('contactsList.columns.contactNumber'),
        cell: ({ row }) => row.original.contactNumber,
        meta: {
          sortField: 'contactNumber',
          pinned: 'left',
          mono: true,
          locked: true,
          exportValue: (row) => row.contactNumber,
        },
      },
      {
        id: 'name',
        header: t('contactsList.columns.name'),
        cell: ({ row }) => <span style={{ fontWeight: 600 }}>{contactName(row.original)}</span>,
        // § Composite cells (FR-17 as amended 2026-09-07) — name carries
        // locality as its secondary: inline at `default` density, stacked
        // at `comfortable`, inline again at `compact` (KAN-577). The `contact` column below
        // is the other composite cell; both use `meta.secondary`, the one
        // mechanism DataGrid already implements.
        meta: {
          sortField: 'lastName',
          locked: true,
          secondary: (row) => row.address?.addressLocality ?? null,
          exportValue: (row) => contactName(row),
        },
      },
      {
        id: 'contactType',
        header: t('contactsList.columns.type'),
        cell: ({ row }) => (
          <ContactTypeBadge type={row.original.contactType} label={translatedContactTypeLabel(t, row.original.contactType)} />
        ),
        meta: {
          filter: { type: 'select', param: 'contactType', options: translatedContactTypeOptions(t), conditions: ['is', 'isNot'] },
          exportValue: (row) => translatedContactTypeLabel(t, row.contactType),
        },
      },
      {
        // FR-17 as amended — the composite Contact cell: mobile primary,
        // email secondary, in ONE cell. When there is no mobile, email
        // takes the primary line and there is no secondary — no stray
        // separator. Both facts are ADR-067 read-model projections the
        // list endpoint already ships on every row.
        id: 'contact',
        header: t('contactsList.columns.contact'),
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
        header: t('contactsList.columns.language'),
        cell: ({ row }) => <LanguageBadge language={row.original.language} />,
        meta: {
          filter: { type: 'select', param: 'language', options: LANGUAGE_OPTIONS, conditions: ['is', 'isNot'] },
          exportValue: (row) => languageEndonym(row.language),
        },
      },
      {
        id: 'lifecycleStatus',
        header: t('contactsList.columns.status'),
        // KAN-464 (Q1): a contact without the customer role has no lifecycle
        // status — only do-not-contact, which belongs to every contact.
        cell: ({ row }) =>
          showsLifecycleStatus(row.original) ? (
            <LifecycleStatusBadge status={row.original.lifecycleStatus} label={translatedLifecycleLabel(t, row.original.lifecycleStatus)} />
          ) : (
            '—'
          ),
        meta: {
          filter: { type: 'select', param: 'lifecycle_status', options: translatedLifecycleOptions(t), conditions: ['is', 'isNot'] },
          exportValue: (row) => (showsLifecycleStatus(row) ? translatedLifecycleLabel(t, row.lifecycleStatus) : ''),
        },
      },
      // KAN-464 — the roles a contact holds (Kunde, Lieferant), filterable
      // (`role` / `roleNot`): suppliers are contacts filtered by role.
      {
        id: 'roles',
        header: t('contactsList.columns.roles'),
        cell: ({ row }) => {
          const roles = row.original.roles ?? []
          return roles.length > 0 ? roles.map((role) => translatedRoleLabel(t, role)).join(', ') : '—'
        },
        meta: {
          defaultVisible: false,
          filter: { type: 'select', param: 'role', options: translatedRoleOptions(t), conditions: ['is', 'isNot'] },
          exportValue: (row) => (row.roles ?? []).map((role) => translatedRoleLabel(t, role)).join(', '),
        },
      },
      // KAN-51 Half 2 (partial) — advisor and tags are already stored
      // (KAN-50) and already flow through ContactRead on every row; only
      // vehicles / open deals / lifetime revenue / last contact (four of
      // the ratified 12 default cells) stay out. test_derived_fields_
      // are_not_writable_in_phase_b2 (tests/contact/test_contact_fr17_fields.py)
      // keeps vehicle_count uncomputed alongside five other Phase-C
      // fields (purchase_count, lifetime_revenue, open_deals,
      // last_contact_at, service_due — only three of which are also grid
      // columns) — read as "these ship as one Relationship-region batch,
      // not staggered," so vehicles isn't cherry-picked out ahead of its
      // still-blocked grid siblings just because its own dependency
      // happens to be lighter.
      {
        id: 'advisor',
        header: t('contactsList.columns.advisor'),
        cell: ({ row }) => row.original.advisorLabel ?? '—',
        meta: {
          // KAN-200: one advisor, or the unassigned queue (FR-A-37).
          filter: {
            type: 'select',
            param: 'advisorUserId',
            options: [{ value: UNASSIGNED_ADVISOR, label: t('contactsList.advisorFilter.unassigned') }, ...advisorOptions],
            conditions: ['is', 'isNot'],
          },
          exportValue: (row) => row.advisorLabel ?? '',
        },
      },
      // KAN-466 — the contact groups, filterable through the shared mapper
      // (`contactGroup` / `contactGroupNot`). The filter offers every group
      // the platform list has: a contact may carry one this Betrieb has not
      // switched on (a sister Betrieb's).
      {
        id: 'contactGroups',
        header: t('contactsList.columns.contactGroups'),
        cell: ({ row }) => {
          const codes = row.original.contactGroups ?? []
          return codes.length > 0 ? codes.map(contactGroups.label).join(', ') : '—'
        },
        meta: {
          // Available, hidden by default: the default cells are the UI spec's
          // ratified set (ADR-060); the user adds it from the Columns panel.
          defaultVisible: false,
          filter: { type: 'select', param: 'contactGroup', options: contactGroupOptions, conditions: ['is', 'isNot'] },
          exportValue: (row) => (row.contactGroups ?? []).map(contactGroups.label).join(', '),
        },
      },
      {
        id: 'tags',
        header: t('contactsList.columns.tags'),
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
      text('salutation', t('contactsList.columns.salutation'), (row) =>
        row.salutation ? translatedSalutationLabel(t, row.salutation) : null,
      ),
      text('contactTitle', t('contactsList.columns.contactTitle'), (row) => row.title),
      text('firstName', t('contactsList.columns.firstName'), (row) => row.firstName),
      text('lastName', t('contactsList.columns.lastName'), (row) => row.lastName),
      text('gender', t('contactsList.columns.gender'), (row) => translatedGenderLabel(t, row.gender)),
      text('companyName', t('contactsList.columns.companyName'), (row) => row.companyName),
      text('legalForm', t('contactsList.columns.legalForm'), (row) => (row.legalForm ? translatedLegalFormLabel(t, row.legalForm) : null)),
      text('website', t('contactsList.columns.website'), (row) => row.website),
      date('birthDate', t('contactsList.columns.birthDate'), (row) => row.birthDate, formatDateOnly),
      text('nationality', t('contactsList.columns.nationality'), (row) => countryLabel(row.nationality)),

      text('phoneMobile', t('contactsList.columns.phoneMobile'), (row) => row.phoneMobile, { mono: true }),
      text('phoneLandline', t('contactsList.columns.phoneLandline'), (row) => row.phoneLandline, { mono: true }),
      text('phoneWork', t('contactsList.columns.phoneWork'), (row) => row.phoneWork, { mono: true }),
      text('email', t('contactsList.columns.email'), (row) => row.email),
      text('emailSecondary', t('contactsList.columns.emailSecondary'), (row) => row.emailSecondary),

      text('addressStreet', t('contactsList.columns.addressStreet'), (row) => row.address?.addressStreet),
      text('addressHouseNumber', t('contactsList.columns.addressHouseNumber'), (row) => row.address?.addressHouseNumber),
      text('addressLine2', t('contactsList.columns.addressLine2'), (row) => row.address?.addressLine2),
      text('addressPostalCode', t('contactsList.columns.addressPostalCode'), (row) => row.address?.addressPostalCode, { mono: true }),
      text('addressLocality', t('contactsList.columns.addressLocality'), (row) => row.address?.addressLocality),
      {
        id: 'addressCanton',
        header: t('contactsList.columns.canton'),
        cell: ({ row }) => row.original.address?.addressCanton ?? '—',
        meta: {
          defaultVisible: false,
          filter: { type: 'select', param: 'canton', options: CANTON_OPTIONS, conditions: ['is', 'isNot'] },
          exportValue: (row) => row.address?.addressCanton ?? '',
        },
      },
      text('addressCountry', t('contactsList.columns.country'), (row) => countryLabel(row.address?.addressCountry)),

      text('preferredChannel', t('contactsList.columns.preferredChannel'), (row) =>
        row.preferredChannel ? translatedPreferredChannelLabel(t, row.preferredChannel) : null,
      ),
      text('source', t('contactsList.columns.source'), (row) => (row.source ? translatedSourceLabel(t, row.source) : null)),
      text('sourceRef', t('contactsList.columns.sourceRef'), (row) => row.sourceRef),
      text('notes', t('contactsList.columns.notes'), (row) => row.notes),
      {
        id: 'marketingConsent',
        header: t('contactsList.columns.marketingConsent'),
        cell: ({ row }) => (row.original.marketingConsent ? '✓' : '—'),
        meta: {
          defaultVisible: false,
          align: 'right',
          exportValue: (row) => (row.marketingConsent ? t('common.yes') : t('common.no')),
        },
      },
      {
        id: 'newsletter',
        header: t('contactsList.columns.newsletter'),
        cell: ({ row }) => (row.original.newsletter ? '✓' : '—'),
        meta: {
          defaultVisible: false,
          align: 'right',
          exportValue: (row) => (row.newsletter ? t('common.yes') : t('common.no')),
        },
      },
      // KAN-50 (FR-17/18 stored fields) — financial/credit facts, hidden
      // by default alongside the other credit-adjacent columns below.
      text('paymentTerms', t('contactsList.columns.paymentTerms'), (row) =>
        row.paymentTerms ? translatedPaymentTermsLabel(t, row.paymentTerms) : null,
      ),
      {
        id: 'creditLimit',
        header: t('contactsList.columns.creditLimit'),
        cell: ({ row }) => (row.original.creditLimit != null ? formatCurrencyChf(Number(row.original.creditLimit)) : '—'),
        // The export keeps the raw decimal, like every other export value.
        meta: { defaultVisible: false, align: 'right', exportValue: (row) => row.creditLimit ?? '' },
      },
      text('iban', t('contactsList.columns.iban'), (row) => row.iban, { mono: true }),
      {
        id: 'vatRegistered',
        header: t('contactsList.columns.vatRegistered'),
        cell: ({ row }) => (row.original.vatRegistered ? '✓' : '—'),
        meta: {
          defaultVisible: false,
          align: 'right',
          exportValue: (row) => (row.vatRegistered ? t('common.yes') : t('common.no')),
        },
      },
      {
        id: 'vatStatusConfirmed',
        header: t('contactsList.columns.vatStatusConfirmed'),
        cell: ({ row }) => (row.original.vatStatusConfirmed ? '✓' : '—'),
        meta: {
          defaultVisible: false,
          align: 'right',
          exportValue: (row) => (row.vatStatusConfirmed ? t('common.yes') : t('common.no')),
        },
      },
      // KAN-44 / FR-18 — the credit block as a grid column, hidden by
      // default, badge-rendered with the reason as its title. reason/
      // blocked-at also get their own columns (KAN-51) — the badge's
      // title attribute is a hover hint, not a substitute for either
      // being independently sortable/exportable/visible on its own.
      {
        id: 'creditBlock',
        header: t('contactsList.columns.creditBlock'),
        cell: ({ row }) =>
          row.original.creditBlock ? (
            <span title={row.original.creditBlockReason ?? undefined}>
              <Badge tone="destructive">{t('contactsList.columns.creditBlock')}</Badge>
            </span>
          ) : (
            '—'
          ),
        meta: {
          defaultVisible: false,
          exportValue: (row) => (row.creditBlock ? (row.creditBlockReason ?? t('common.yes')) : ''),
        },
      },
      text('creditBlockReason', t('contactsList.columns.creditBlockReason'), (row) => row.creditBlockReason),
      date('creditBlockedAt', t('contactsList.columns.creditBlockedAt'), (row) => row.creditBlockedAt),
      date('contactSince', t('contactsList.columns.contactSince'), (row) => row.contactSince, formatDateOnly),
      date('nextFollowUp', t('contactsList.columns.nextFollowUp'), (row) => row.nextFollowUp, formatDateOnly),
      date('createdAt', t('contactsList.columns.created'), (row) => row.createdAt),
      {
        id: 'updatedAt',
        header: t('contactsList.columns.changed'),
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
            conditions: ['today', 'thisWeek', 'thisMonth', 'thisYear', 'inTheLastDays', 'moreThanDaysAgo'],
          },
          exportValue: (row) => formatDate(row.updatedAt, locale),
        },
      },
      text('duplicateOfContactId', t('contactsList.columns.duplicateOf'), (row) => row.duplicateOfContactId, { mono: true }),
    ]
  }, [t, locale, countryLabel, advisorOptions, contactGroups, contactGroupOptions])

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
  // KAN-458: a predicate from the URL or a saved view that this grid does
  // not offer is neither sent nor shown as an active filter.
  const predicates = offeredPredicates(urlPredicates, filterFields)

  // The effective layout: the user's saved one, or the module default
  // (which honours each column's `defaultVisible`). DataGrid,
  // ColumnConfigPanel and the export column set all read the SAME value,
  // so "what you see" is one definition — never the grid showing every
  // column just because no preference has been saved yet.
  const columnLayout = useMemo(
    () => gridPrefs.columnLayout ?? defaultColumnLayout(columnRegistry),
    [gridPrefs.columnLayout, columnRegistry]
  )
  const columnPanelLabels = useColumnPanelLabels()
  const viewsAndFiltersLabels = useViewsAndFiltersLabels()
  const actionBarLabels = useActionBarLabels()

  const sortParam = sortSpecToQueryParam(sort)

  const { data, fetchNextPage, hasNextPage, isFetchingNextPage, isLoading, isError, refetch, isRefetching } =
    useInfiniteQuery({
      queryKey: ['contacts', GRID_KEY, debouncedQuery, sortParam, predicates],
      queryFn: async ({ pageParam }: { pageParam: string | null }) => {
        const params = new URLSearchParams()
        if (debouncedQuery) params.set('q', debouncedQuery)
        if (sortParam) params.set('sort', sortParam)
        applyContactFilters(params, predicates, filterFields)
        params.set('limit', '50')
        if (pageParam) params.set('cursor', pageParam)
        return api.get<ContactPage>(`/contacts?${params.toString()}`)
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

  const exportColumns: ExportColumn<ContactRead>[] = useMemo(() => {
    const byId = new Map(columns.map((c) => [String(c.id), c]))
    return visibleColumnIds
      .map((id) => byId.get(id))
      .filter((c): c is GridColumnDef<ContactRead> => Boolean(c))
      .map((c) => ({
        header: typeof c.header === 'string' ? c.header : String(c.id),
        value: (row: ContactRead) => {
          const raw = c.meta?.exportValue?.(row)
          return raw == null ? '' : String(raw)
        },
      }))
  }, [columns, visibleColumnIds])

  const selectedRows = useMemo(() => rows.filter((row) => selectedIds.has(row.id)), [rows, selectedIds])

  const exportSelection = () => {
    const stamp = todayLocalIsoDate()
    exportRowsToCsv(`contacts_${stamp}.csv`, exportColumns, selectedRows)
  }
  const printSelection = () => {
    printRows(t('contactsList.title'), exportColumns, selectedRows)
  }

  // Row-menu handlers this surface can do without a modal (link-vehicle
  // and merge stay on the detail screen — same posture ValuationsListPage
  // takes). "New offer" is the same POST-then-PATCH sequence
  // ContactDetailPage runs, started from the row.
  //
  // KAN-266 — each passes its contact as the request: a retry for the same
  // contact keeps its key and replays the first create, another contact
  // gets a new key. As on the contact screen, a retry after a PATCH that
  // landed with its response lost is a version conflict, not a second offer.
  //
  // KAN-273 (ADR-035): a failed create is said above the grid; the retry
  // from the same row carries the same key.
  const createOfferForContact = async (contactId: string) => {
    setActionError(null)
    try {
      const created = await api.post<SalesOfferRead>('/sales/offers', undefined, offerCreateKey.headers([contactId]))
      const updated = await api.patch<SalesOfferRead>(
        `/sales/offers/${created.id}`,
        { contactId },
        { 'If-Match': String(created.version) },
      )
      offerCreateKey.renew()
      navigate(`/sales/offers/${updated.id}`)
    } catch (err) {
      setActionError(apiRefusalText(t, err, 'contactsList.createOfferError'))
    }
  }

  // KAN-58 — "New contract", the contact→contract entry point. Simpler
  // than the offer flow above: ContractCreate accepts contactId directly,
  // so this is one POST, not POST-then-PATCH.
  const createContractForContact = async (contactId: string) => {
    setActionError(null)
    try {
      const body = { contactId }
      const created = await api.post<SalesContractRead>('/sales/contracts', body, contractCreateKey.headers(body))
      contractCreateKey.renew()
      navigate(`/sales/contracts/${created.id}`)
    } catch (err) {
      setActionError(apiRefusalText(t, err, 'contactsList.createContractError'))
    }
  }

  // KAN-308 (ADR-035): a refused toggle is said above the grid like a
  // failed create — a user who believes they flagged a contact
  // do-not-contact (ADR-065) must not be left thinking so. A version
  // conflict means the row changed elsewhere: the grid is refetched so the
  // next click carries the current version.
  const toggleDoNotContact = async (row: ContactRead) => {
    setActionError(null)
    try {
      await api.patch<ContactRead>(
        `/contacts/${row.id}`,
        { lifecycleStatus: row.lifecycleStatus === 'do_not_contact' ? 'active' : 'do_not_contact' },
        { 'If-Match': String(row.version) },
      )
      void queryClient.invalidateQueries({ queryKey: ['contacts'] })
    } catch (err) {
      setActionError(apiRefusalText(t, err, 'contactsList.doNotContactError'))
      if (err instanceof ApiError && err.status === 409) void queryClient.invalidateQueries({ queryKey: ['contacts'] })
    }
  }

  const isFiltered = debouncedQuery.length > 0 || predicates.length > 0
  const currentViewName = (appliedViewId && savedViews.views.find((v) => v.id === appliedViewId)?.name) || t('contactsList.allContactsView')

  const applyView = (view: SavedView) => {
    setAppliedViewId(view.id)
    if (view.snapshot.sort) setSort(view.snapshot.sort)
    if (view.snapshot.columnLayout) gridPrefs.setColumnLayout(view.snapshot.columnLayout)
    if (view.snapshot.filters) setPredicates(view.snapshot.filters)
  }

  return (
    <Stack gap="md">
      <Group justify="space-between">
        <Title order={2}>{t('contactsList.title')}</Title>
        <Button onClick={() => setCreateOpen(true)}>{t('contactsList.newContact')}</Button>
      </Group>

      {actionError && (
        <Alert color="red" role="alert" withCloseButton closeButtonLabel={t('common.dismiss')} onClose={() => setActionError(null)}>
          {actionError}
        </Alert>
      )}

      <OverviewShellRegion
        header={<div />}
        actionBar={
          <>
            <ActionBar
              labels={actionBarLabels}
              searchValue={query}
              onSearchChange={setQuery}
              searchPlaceholder={t('contactsList.searchPlaceholder')}
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

            {selectedIds.size > 0 ? (
              <SelectionBar
                count={selectedIds.size}
                onClear={() => setSelectedIds(new Set())}
                countLabel={(n) => t('contactsList.selection.count', { count: n })}
                clearLabel={t('contactsList.selection.clear')}
                actions={[
                  {
                    // D-25 — the SELECTED rows in the CURRENTLY VISIBLE
                    // columns, as a real CSV file. Not "copy IDs".
                    label: t('contactsList.selection.export'),
                    icon: <Download size={14} />,
                    onClick: exportSelection,
                  },
                  {
                    label: t('contactsList.selection.print'),
                    icon: <Printer size={14} />,
                    onClick: printSelection,
                  },
                ]}
              />
            ) : null}
          </>
        }
      >
        <DataGrid<ContactRead>
          columns={columns}
          rows={rows}
          getRowId={(row) => row.id}
          sort={sort}
          onSortChange={setSort}
          density={density}
          rowHref={(row) => `/contacts/${row.id}`}
          linkComponent={Link}
          loading={isLoading}
          refetching={isRefetching && !isLoading}
          fetchingNextPage={isFetchingNextPage}
          hasNextPage={Boolean(hasNextPage)}
          onLoadMore={() => fetchNextPage()}
          error={isError ? t('contactsList.loadError') : null}
          onRetry={() => refetch()}
          total={total}
          totalIsEstimate={totalIsEstimate}
          isFiltered={isFiltered}
          selection={{
            selectedIds,
            onSelectionChange: setSelectedIds,
            selectAllLabel: t('common.selectAllLabel'),
            selectRowLabel: (row) => t('common.selectRowLabel', { label: contactName(row) || row.contactNumber }),
          }}
          columnLayout={columnLayout}
          onColumnLayoutChange={gridPrefs.setColumnLayout}
          labels={dataGridLabels}
          emptyState={{
            icon: <Users size={24} />,
            title: t('contactsList.emptyState.title'),
            description: t('contactsList.emptyState.description'),
            action: <Button onClick={() => setCreateOpen(true)}>{t('contactsList.newContact')}</Button>,
          }}
          emptyFilteredState={{
            icon: <Users size={24} />,
            title: t('contactsList.emptyFilteredState.title'),
            description: t('contactsList.emptyFilteredState.description'),
            action: (
              <Button
                variant="default"
                onClick={() => {
                  setQuery('')
                  setPredicates([])
                  setAppliedViewId(null)
                }}
              >
                {t('contactsList.emptyFilteredState.action')}
              </Button>
            ),
          }}
          rowActions={(row) => {
            // KAN-44 / ADR-061 — the SAME builder the detail screen's
            // header/overflow uses, so "New offer" / "New contract" can't
            // drift between the two surfaces. Link-vehicle and merge are
            // modal-heavy and stay on the detail screen.
            const menu = buildContactRowMenu(t, row, {
              onNewOffer: salesEntitled ? () => void createOfferForContact(row.id) : undefined,
              onNewContract: salesEntitled ? () => void createContractForContact(row.id) : undefined,
              onCopyContactNumber: () => void navigator.clipboard.writeText(row.contactNumber),
              onToggleDoNotContact: () => void toggleDoNotContact(row),
              onManageCreditBlock: () => setBlockDialogContact(row),
            }, { mayWriteCustomerRole })
            return {
              navigate: [
                {
                  label: t('contactsList.rowActions.open'),
                  icon: <ExternalLink size={16} />,
                  onClick: () => navigate(`/contacts/${row.id}`),
                },
              ],
              ...menu.overflow,
            }
          }}
        />
      </OverviewShellRegion>

      {blockDialogContact && (
        <CreditBlockDialog
          opened
          onClose={() => setBlockDialogContact(null)}
          contact={blockDialogContact}
          onSaved={() => {
            setBlockDialogContact(null)
            void queryClient.invalidateQueries({ queryKey: ['contacts'] })
          }}
        />
      )}

      <ContactCreateDialog
        opened={createOpen}
        onClose={() => setCreateOpen(false)}
        onCreated={(contact) => {
          setCreateOpen(false)
          void queryClient.invalidateQueries({ queryKey: ['contacts'] })
          navigate(`/contacts/${contact.id}`)
        }}
        onOpenExisting={(contactId) => navigate(`/contacts/${contactId}`)}
      />
    </Stack>
  )
}
