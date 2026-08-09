import { useMemo, useState } from 'react'
import { Link, useNavigate } from 'react-router-dom'
import { Button, Group, Menu, Stack, Title } from '@mantine/core'
import { useDebouncedValue } from '@mantine/hooks'
import { useInfiniteQuery } from '@tanstack/react-query'
import { Copy, ExternalLink, Users } from 'lucide-react'
import { useTranslation } from 'react-i18next'
import {
  ActionBar,
  CustomerTypeBadge,
  DataGrid,
  FilterButton,
  FilterChips,
  LanguageBadge,
  LifecycleStatusBadge,
  useSetBreadcrumb,
  type GridColumnDef,
  type SortSpec,
} from '@nexotec/ui-kit'
import { useUiPreferencesContext } from '../hooks/UiPreferencesContext'
import { api } from '../api/client'
import { toSwissLocale, type SupportedLanguage } from '../i18n'
import { CANTON_OPTIONS, translatedCustomerTypeLabel, translatedLanguageOptions, translatedLifecycleLabel } from '../customerOptions'
import {
  CustomerFiltersPopover,
  countActiveFilters,
  EMPTY_CUSTOMER_FILTERS,
  type CustomerFilters,
} from '../components/CustomerFiltersPopover'
import { formatDate } from '../utils/format'
import type { CustomerPage, CustomerRead } from '../api/types'

const GRID_KEY = 'mdm.customers.list'
const DEFAULT_SORT: SortSpec[] = [{ field: 'updatedAt', direction: 'desc' }]

function customerName(c: CustomerRead): string {
  return c.customerType === 'business' ? (c.companyName ?? '') : `${c.firstName ?? ''} ${c.lastName ?? ''}`.trim()
}

export function CustomersListPage() {
  const { t, i18n } = useTranslation()
  const locale = toSwissLocale(i18n.language as SupportedLanguage)
  useSetBreadcrumb([t('shell.nav.masterData'), t('shell.nav.customers')])
  const navigate = useNavigate()
  const { density, setDensity } = useUiPreferencesContext()

  const [query, setQuery] = useState('')
  const [debouncedQuery] = useDebouncedValue(query, 250)
  const [sort, setSort] = useState<SortSpec[]>(DEFAULT_SORT)
  const [filters, setFilters] = useState<CustomerFilters>(EMPTY_CUSTOMER_FILTERS)
  const [filterPopoverOpened, setFilterPopoverOpened] = useState(false)
  const activeFilterCount = countActiveFilters(filters)

  const sortParam = sort.length > 0 ? sort.map((s) => `${s.field}:${s.direction}`).join(',') : undefined

  const { data, fetchNextPage, hasNextPage, isFetchingNextPage, isLoading, isError, refetch, isRefetching } =
    useInfiniteQuery({
      queryKey: ['customers', GRID_KEY, debouncedQuery, sortParam, filters],
      queryFn: async ({ pageParam }: { pageParam: string | null }) => {
        const params = new URLSearchParams()
        if (debouncedQuery) params.set('q', debouncedQuery)
        if (sortParam) params.set('sort', sortParam)
        if (filters.customerType) params.set('customer_type', filters.customerType)
        if (filters.lifecycleStatus) params.set('lifecycle_status', filters.lifecycleStatus)
        if (filters.language) params.set('language', filters.language)
        if (filters.canton) params.set('canton', filters.canton)
        if (filters.changedSince) params.set('updated_since', filters.changedSince)
        params.set('limit', '50')
        if (pageParam) params.set('cursor', pageParam)
        return api.get<CustomerPage>(`/customers?${params.toString()}`)
      },
      initialPageParam: null as string | null,
      getNextPageParam: (lastPage) => lastPage.nextCursor,
    })

  const filterChips = useMemo(() => {
    const chips: { key: string; label: string; onRemove: () => void }[] = []
    if (filters.customerType) {
      chips.push({
        key: 'customerType',
        label: translatedCustomerTypeLabel(t, filters.customerType),
        onRemove: () => setFilters((f) => ({ ...f, customerType: null })),
      })
    }
    if (filters.lifecycleStatus) {
      chips.push({
        key: 'lifecycleStatus',
        label: translatedLifecycleLabel(t, filters.lifecycleStatus),
        onRemove: () => setFilters((f) => ({ ...f, lifecycleStatus: null })),
      })
    }
    if (filters.language) {
      chips.push({
        key: 'language',
        label: translatedLanguageOptions(t).find((o) => o.value === filters.language)?.label ?? filters.language,
        onRemove: () => setFilters((f) => ({ ...f, language: null })),
      })
    }
    if (filters.canton) {
      chips.push({
        key: 'canton',
        label: CANTON_OPTIONS.find((o) => o.value === filters.canton)?.label ?? filters.canton,
        onRemove: () => setFilters((f) => ({ ...f, canton: null })),
      })
    }
    if (filters.changedSince) {
      chips.push({
        key: 'changedSince',
        label: t('customersList.filters.changedSinceChip', { date: filters.changedSince }),
        onRemove: () => setFilters((f) => ({ ...f, changedSince: null })),
      })
    }
    return chips
  }, [filters, t])

  const rows = useMemo(() => data?.pages.flatMap((page) => page.items) ?? [], [data])
  const total = data?.pages[0]?.total ?? null
  const totalIsEstimate = data?.pages[0]?.totalIsEstimate ?? false

  const columns: GridColumnDef<CustomerRead>[] = useMemo(
    () => [
      {
        id: 'customerNumber',
        header: t('customersList.columns.customerNumber'),
        cell: ({ row }) => row.original.customerNumber,
        meta: { sortField: 'customerNumber', pinned: 'left', mono: true },
      },
      {
        id: 'name',
        header: t('customersList.columns.name'),
        cell: ({ row }) => <span style={{ fontWeight: 600 }}>{customerName(row.original)}</span>,
        meta: { sortField: 'lastName' },
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
    ],
    [t, locale]
  )

  return (
    <Stack gap="md">
      <Group justify="space-between">
        <Title order={2}>{t('customersList.title')}</Title>
        <Button component={Link} to="/customers/new">
          {t('customersList.newCustomer')}
        </Button>
      </Group>

      <ActionBar
        searchValue={query}
        onSearchChange={setQuery}
        searchPlaceholder={t('customersList.searchPlaceholder')}
        density={density}
        onDensityChange={setDensity}
        onRefresh={() => refetch()}
        refreshing={isRefetching}
        filterSlot={
          <FilterButton
            activeCount={activeFilterCount}
            opened={filterPopoverOpened}
            onChange={setFilterPopoverOpened}
            label={t('common.filter')}
          >
            <CustomerFiltersPopover filters={filters} onChange={setFilters} />
          </FilterButton>
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

      <FilterChips
        chips={filterChips}
        onClearAll={() => setFilters(EMPTY_CUSTOMER_FILTERS)}
        clearLabel={t('common.clear')}
        removeLabel={(label) => t('common.removeFilter', { label })}
      />

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
        fetchingNextPage={isFetchingNextPage}
        hasNextPage={Boolean(hasNextPage)}
        onLoadMore={() => fetchNextPage()}
        error={isError ? 'Failed to load customers.' : null}
        onRetry={() => refetch()}
        total={total}
        totalIsEstimate={totalIsEstimate}
        isFiltered={debouncedQuery.length > 0 || activeFilterCount > 0}
        locale={locale}
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
                setFilters(EMPTY_CUSTOMER_FILTERS)
              }}
            >
              {t('customersList.emptyFilteredState.action')}
            </Button>
          ),
        }}
        rowActions={(row) => (
          <>
            <Menu.Item leftSection={<ExternalLink size={16} />} onClick={() => navigate(`/customers/${row.id}`)}>
              {t('customersList.rowActions.open')}
            </Menu.Item>
            <Menu.Item
              leftSection={<Copy size={16} />}
              onClick={() => navigator.clipboard.writeText(row.customerNumber)}
            >
              {t('customersList.rowActions.copyCustomerNumber')}
            </Menu.Item>
          </>
        )}
      />
    </Stack>
  )
}
