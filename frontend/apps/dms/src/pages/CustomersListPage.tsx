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
  LanguageBadge,
  LifecycleStatusBadge,
  useSetBreadcrumb,
  type GridColumnDef,
  type SortSpec,
} from '@nexotec/ui-kit'
import { useUiPreferencesContext } from '../hooks/UiPreferencesContext'
import { api } from '../api/client'
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

  const sortParam = sort.length > 0 ? sort.map((s) => `${s.field}:${s.direction}`).join(',') : undefined

  const { data, fetchNextPage, hasNextPage, isFetchingNextPage, isLoading, isError, refetch, isRefetching } =
    useInfiniteQuery({
      queryKey: ['customers', GRID_KEY, debouncedQuery, sortParam],
      queryFn: async ({ pageParam }: { pageParam: string | null }) => {
        const params = new URLSearchParams()
        if (debouncedQuery) params.set('q', debouncedQuery)
        if (sortParam) params.set('sort', sortParam)
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
        isFiltered={debouncedQuery.length > 0}
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
          description: 'Try a different search term.',
          action: (
            <Button variant="default" onClick={() => setQuery('')}>
              Clear search
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
