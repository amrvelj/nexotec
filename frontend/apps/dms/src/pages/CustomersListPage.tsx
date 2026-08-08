import { useMemo, useState } from 'react'
import { Link, useNavigate } from 'react-router-dom'
import { Button, Group, Menu, Stack, Title } from '@mantine/core'
import { useDebouncedValue } from '@mantine/hooks'
import { useInfiniteQuery } from '@tanstack/react-query'
import { Copy, ExternalLink, Users } from 'lucide-react'
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
import { CANTON_OPTIONS, CUSTOMER_TYPE_OPTIONS, LANGUAGE_OPTIONS, LIFECYCLE_OPTIONS } from '../customerOptions'
import {
  CustomerFiltersPopover,
  countActiveFilters,
  EMPTY_CUSTOMER_FILTERS,
  type CustomerFilters,
} from '../components/CustomerFiltersPopover'
import type { CustomerPage, CustomerRead } from '../api/types'

const GRID_KEY = 'mdm.customers.list'
const DEFAULT_SORT: SortSpec[] = [{ field: 'updatedAt', direction: 'desc' }]

function customerName(c: CustomerRead): string {
  return c.customerType === 'business' ? (c.companyName ?? '') : `${c.firstName ?? ''} ${c.lastName ?? ''}`.trim()
}

function formatDate(iso: string): string {
  return new Intl.DateTimeFormat('de-CH', { day: '2-digit', month: '2-digit', year: 'numeric' }).format(new Date(iso))
}

export function CustomersListPage() {
  useSetBreadcrumb(['Master Data', 'Customers'])
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
        label: CUSTOMER_TYPE_OPTIONS.find((o) => o.value === filters.customerType)?.label ?? filters.customerType,
        onRemove: () => setFilters((f) => ({ ...f, customerType: null })),
      })
    }
    if (filters.lifecycleStatus) {
      chips.push({
        key: 'lifecycleStatus',
        label: LIFECYCLE_OPTIONS.find((o) => o.value === filters.lifecycleStatus)?.label ?? filters.lifecycleStatus,
        onRemove: () => setFilters((f) => ({ ...f, lifecycleStatus: null })),
      })
    }
    if (filters.language) {
      chips.push({
        key: 'language',
        label: LANGUAGE_OPTIONS.find((o) => o.value === filters.language)?.label ?? filters.language,
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
        label: `Changed since ${filters.changedSince}`,
        onRemove: () => setFilters((f) => ({ ...f, changedSince: null })),
      })
    }
    return chips
  }, [filters])

  const rows = useMemo(() => data?.pages.flatMap((page) => page.items) ?? [], [data])
  const total = data?.pages[0]?.total ?? null
  const totalIsEstimate = data?.pages[0]?.totalIsEstimate ?? false

  const columns: GridColumnDef<CustomerRead>[] = useMemo(
    () => [
      {
        id: 'customerNumber',
        header: 'Customer #',
        cell: ({ row }) => row.original.customerNumber,
        meta: { sortField: 'customerNumber', pinned: 'left', mono: true },
      },
      {
        id: 'name',
        header: 'Name',
        cell: ({ row }) => <span style={{ fontWeight: 600 }}>{customerName(row.original)}</span>,
        meta: { sortField: 'lastName' },
      },
      {
        id: 'customerType',
        header: 'Type',
        cell: ({ row }) => <CustomerTypeBadge type={row.original.customerType} />,
      },
      {
        id: 'language',
        header: 'Language',
        cell: ({ row }) => <LanguageBadge language={row.original.language} />,
      },
      {
        id: 'lifecycleStatus',
        header: 'Status',
        cell: ({ row }) => <LifecycleStatusBadge status={row.original.lifecycleStatus} />,
      },
      {
        id: 'updatedAt',
        header: 'Changed',
        cell: ({ row }) => formatDate(row.original.updatedAt),
        meta: { sortField: 'updatedAt', align: 'right' },
      },
    ],
    []
  )

  return (
    <Stack gap="md">
      <Group justify="space-between">
        <Title order={2}>Customers</Title>
        <Button component={Link} to="/customers/new">
          New customer
        </Button>
      </Group>

      <ActionBar
        searchValue={query}
        onSearchChange={setQuery}
        searchPlaceholder="Search by name, email, phone…"
        density={density}
        onDensityChange={setDensity}
        onRefresh={() => refetch()}
        refreshing={isRefetching}
        filterSlot={
          <FilterButton activeCount={activeFilterCount} opened={filterPopoverOpened} onChange={setFilterPopoverOpened}>
            <CustomerFiltersPopover filters={filters} onChange={setFilters} />
          </FilterButton>
        }
      />

      <FilterChips chips={filterChips} onClearAll={() => setFilters(EMPTY_CUSTOMER_FILTERS)} />

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
        emptyState={{
          icon: <Users size={24} />,
          title: 'No customers yet',
          description: 'Create your first customer to get started.',
          action: (
            <Button component={Link} to="/customers/new">
              New customer
            </Button>
          ),
        }}
        emptyFilteredState={{
          icon: <Users size={24} />,
          title: 'No matches',
          description: 'Try a different search term or filter.',
          action: (
            <Button
              variant="default"
              onClick={() => {
                setQuery('')
                setFilters(EMPTY_CUSTOMER_FILTERS)
              }}
            >
              Clear search and filters
            </Button>
          ),
        }}
        rowActions={(row) => (
          <>
            <Menu.Item leftSection={<ExternalLink size={16} />} onClick={() => navigate(`/customers/${row.id}`)}>
              Open
            </Menu.Item>
            <Menu.Item
              leftSection={<Copy size={16} />}
              onClick={() => navigator.clipboard.writeText(row.customerNumber)}
            >
              Copy customer number
            </Menu.Item>
          </>
        )}
      />
    </Stack>
  )
}
