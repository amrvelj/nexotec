import { useMemo, useState } from 'react'
import { Stack, Text, Title } from '@mantine/core'
import { useDebouncedValue } from '@mantine/hooks'
import { useInfiniteQuery, useQuery, useQueryClient } from '@tanstack/react-query'
import { AlertTriangle, Building2 } from 'lucide-react'
import { useTranslation } from 'react-i18next'
import {
  ActionBar,
  ConnectionStatusBadge,
  DataGrid,
  OverviewShellRegion,
  ViewsAndFilters,
  type GridColumnDef,
  type SortSpec,
} from '@nexotec/ui-kit'
import { api } from '../api/client'
import { buildConnectionRowMenu } from '../components/connectionRowMenu'
import { useSavedViews } from '../hooks/useSavedViews'
import { useUiPreferencesContext } from '../hooks/UiPreferencesContext'
import { toSwissLocale, type SupportedLanguage } from '../i18n'
import { formatDate, formatDateTime } from '../utils/format'
import type {
  CatalogueSyncStatusRead,
  DealershipPage,
  IntegrationConnectionPage,
  IntegrationConnectionRead,
} from '../api/types'

const CONNECTIONS_GRID_KEY = 'integrations.platform.connections'

/**
 * WP-6 PR-7 — platform view: every connection across every tenant
 * (filterable), plus the fleet-wide health board. Both are a `DataGrid`
 * — the health board explicitly never a bespoke table (the brief's own
 * instruction, confirmed against the prototype: no such component exists
 * to reach for by mistake). Composed from two independent reads
 * (app.integration's own connection list, app.vehicle's own sync-status
 * endpoint) joined client-side against a third (the dealership list, for
 * display names) — never a cross-context reach-in on either backend.
 */
export function IntegrationPlatformView() {
  const { t, i18n } = useTranslation()
  const locale = toSwissLocale(i18n.language as SupportedLanguage)
  const { density, setDensity } = useUiPreferencesContext()
  const queryClient = useQueryClient()
  const savedViews = useSavedViews(CONNECTIONS_GRID_KEY)

  // Capped at the API's own max page size (100) — a name lookup for
  // display purposes, not a paginated browse; a group with more
  // dealerships than that is out of scope for this first cut (flagged,
  // not silently wrong: a connection whose tenant isn't in this map
  // simply shows its raw tenant id instead of a name).
  const dealershipsQuery = useQuery({
    queryKey: ['dealerships', 'all-for-integrations'],
    queryFn: () => api.get<DealershipPage>('/dealerships?limit=100'),
  })
  const dealershipNameById = useMemo(() => {
    const map = new Map<string, string>()
    for (const d of dealershipsQuery.data?.items ?? []) map.set(d.id, d.legalName)
    return map
  }, [dealershipsQuery.data])

  const [query, setQuery] = useState('')
  const [debouncedQuery] = useDebouncedValue(query, 250)
  const [sort, setSort] = useState<SortSpec[]>([])

  const connectionsQuery = useInfiniteQuery({
    queryKey: ['integrations', 'connections', 'platform'],
    queryFn: async ({ pageParam }: { pageParam: string | null }) => {
      const params = new URLSearchParams()
      params.set('limit', '100')
      if (pageParam) params.set('cursor', pageParam)
      return api.get<IntegrationConnectionPage>(`/integrations/connections?${params.toString()}`)
    },
    initialPageParam: null as string | null,
    getNextPageParam: (lastPage) => lastPage.nextCursor,
  })

  const allConnections = useMemo(
    () => connectionsQuery.data?.pages.flatMap((page) => page.items) ?? [],
    [connectionsQuery.data]
  )
  const rows = useMemo(
    () =>
      debouncedQuery
        ? allConnections.filter(
            (c) =>
              c.displayName.toLowerCase().includes(debouncedQuery.toLowerCase()) ||
              c.providerCode.toLowerCase().includes(debouncedQuery.toLowerCase())
          )
        : allConnections,
    [allConnections, debouncedQuery]
  )

  const invalidate = () => queryClient.invalidateQueries({ queryKey: ['integrations'] })
  const test = async (connection: IntegrationConnectionRead) => {
    await api.post(`/integrations/connections/${connection.id}/test`)
    await invalidate()
  }
  const toggleEnabled = async (connection: IntegrationConnectionRead) => {
    await api.post(`/integrations/connections/${connection.id}/${connection.enabled ? 'disable' : 'enable'}`)
    await invalidate()
  }

  const columns: GridColumnDef<IntegrationConnectionRead>[] = useMemo(
    () => [
      {
        id: 'dealership',
        header: t('integrationsList.columns.dealership'),
        cell: ({ row }) =>
          row.original.tenantId
            ? (dealershipNameById.get(row.original.tenantId) ?? row.original.tenantId)
            : t('integrationsList.platformScoped'),
      },
      { id: 'displayName', header: t('integrationsList.columns.displayName'), cell: ({ row }) => row.original.displayName },
      { id: 'providerCode', header: t('integrationsList.columns.provider'), cell: ({ row }) => row.original.providerCode },
      {
        id: 'environment',
        header: t('integrationsList.columns.environment'),
        cell: ({ row }) => t(`integrationEnums.environment.${row.original.environment}`, row.original.environment),
      },
      { id: 'status', header: t('integrationsList.columns.status'), cell: ({ row }) => <ConnectionStatusBadge status={row.original.status} /> },
      {
        id: 'lastVerifiedAt',
        header: t('integrationsList.columns.lastVerified'),
        cell: ({ row }) => (row.original.lastVerifiedAt ? formatDate(row.original.lastVerifiedAt, locale) : '—'),
        meta: { align: 'right' },
      },
    ],
    [t, locale, dealershipNameById]
  )

  const currentViewName = t('integrationsList.allView')

  return (
    <Stack gap="xl">
      <Stack gap="md">
        <Title order={3}>{t('integrationsList.platformConnectionsTitle')}</Title>
        <OverviewShellRegion
          header={<div />}
          actionBar={
            <ActionBar
              searchValue={query}
              onSearchChange={setQuery}
              searchPlaceholder={t('integrationsList.searchPlaceholder')}
              density={density}
              onDensityChange={setDensity}
              onRefresh={() => connectionsQuery.refetch()}
              refreshing={connectionsQuery.isRefetching}
              filterSlot={
                <ViewsAndFilters
                  currentViewName={currentViewName}
                  views={savedViews.views}
                  onApplyView={() => {}}
                  onSaveCurrentAsView={(name) => savedViews.saveView(name, { sort, filters: [] })}
                  onDeleteView={savedViews.deleteView}
                  onSetDefaultView={savedViews.setDefaultView}
                  fields={[]}
                  predicates={[]}
                  onPredicatesChange={() => {}}
                  onResetFilters={() => {}}
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
          }
        >
          <DataGrid<IntegrationConnectionRead>
            columns={columns}
            rows={rows}
            getRowId={(row) => row.id}
            sort={sort}
            onSortChange={setSort}
            density={density}
            loading={connectionsQuery.isLoading}
            fetchingNextPage={connectionsQuery.isFetchingNextPage}
            hasNextPage={Boolean(connectionsQuery.hasNextPage)}
            onLoadMore={() => connectionsQuery.fetchNextPage()}
            error={connectionsQuery.isError ? t('integrationsList.loadError') : null}
            onRetry={() => connectionsQuery.refetch()}
            total={null}
            totalIsEstimate={false}
            isFiltered={debouncedQuery.length > 0}
            labels={{
              showing: (count) => t('common.showing', { count }),
              loadingMore: t('common.loadingMore'),
              retry: t('common.retry'),
              rowActionsLabel: t('common.rowActionsLabel'),
            }}
            emptyState={{
              icon: <Building2 size={24} />,
              title: t('integrationsList.emptyState.title'),
              description: t('integrationsList.emptyState.description'),
            }}
            emptyFilteredState={{
              icon: <Building2 size={24} />,
              title: t('integrationsList.emptyFilteredState.title'),
              description: t('integrationsList.emptyFilteredState.description'),
            }}
            rowActions={(row) =>
              buildConnectionRowMenu(t, row, {
                onTest: () => void test(row),
                onViewUsage: () => {},
                onRotateSecret: () => {},
                onToggleEnabled: () => void toggleEnabled(row),
              })
            }
          />
        </OverviewShellRegion>
      </Stack>

      <FleetHealthBoard locale={locale} dealershipNameById={dealershipNameById} />
    </Stack>
  )
}

function FleetHealthBoard({
  locale,
  dealershipNameById,
}: {
  locale: string
  dealershipNameById: Map<string, string>
}) {
  const { t } = useTranslation()
  const { density } = useUiPreferencesContext()

  const statusQuery = useQuery({
    queryKey: ['vehicle-mdm', 'catalogue-sync-status'],
    queryFn: () => api.get<CatalogueSyncStatusRead[]>('/vehicle-mdm/catalogue-sync-status'),
  })

  const columns: GridColumnDef<CatalogueSyncStatusRead>[] = useMemo(
    () => [
      {
        id: 'dealership',
        header: t('integrationsList.columns.dealership'),
        cell: ({ row }) => dealershipNameById.get(row.original.tenantId) ?? row.original.tenantId,
      },
      { id: 'providerCode', header: t('integrationsList.columns.provider'), cell: ({ row }) => row.original.providerCode },
      {
        id: 'lastFullSeedAt',
        header: t('integrationsList.healthBoard.lastFullSeed'),
        cell: ({ row }) => (row.original.lastFullSeedAt ? formatDateTime(row.original.lastFullSeedAt, locale) : '—'),
      },
      {
        id: 'lastSystemWatermarkDate',
        header: t('integrationsList.healthBoard.providerWatermark'),
        cell: ({ row }) => (row.original.lastSystemWatermarkDate ? formatDate(row.original.lastSystemWatermarkDate, locale) : '—'),
      },
      {
        id: 'stale',
        header: t('integrationsList.healthBoard.status'),
        cell: ({ row }) =>
          row.original.stale ? (
            <Text size="sm" c="red" fw={600}>{t('integrationsList.healthBoard.stale')}</Text>
          ) : (
            <Text size="sm" c="green">{t('integrationsList.healthBoard.healthy')}</Text>
          ),
      },
    ],
    [t, locale, dealershipNameById]
  )

  return (
    <Stack gap="md">
      <Title order={3}>{t('integrationsList.healthBoard.title')}</Title>
      <DataGrid<CatalogueSyncStatusRead>
        columns={columns}
        rows={statusQuery.data ?? []}
        getRowId={(row) => `${row.tenantId}:${row.providerCode}`}
        sort={[]}
        onSortChange={() => {}}
        density={density}
        loading={statusQuery.isLoading}
        fetchingNextPage={false}
        hasNextPage={false}
        onLoadMore={() => {}}
        error={statusQuery.isError ? t('integrationsList.loadError') : null}
        onRetry={() => statusQuery.refetch()}
        total={null}
        totalIsEstimate={false}
        isFiltered={false}
        labels={{
          showing: (count) => t('common.showing', { count }),
          loadingMore: t('common.loadingMore'),
          retry: t('common.retry'),
          rowActionsLabel: t('common.rowActionsLabel'),
        }}
        emptyState={{
          icon: <AlertTriangle size={24} />,
          title: t('integrationsList.healthBoard.emptyState.title'),
          description: t('integrationsList.healthBoard.emptyState.description'),
        }}
        emptyFilteredState={{
          icon: <AlertTriangle size={24} />,
          title: t('integrationsList.healthBoard.emptyState.title'),
          description: t('integrationsList.healthBoard.emptyState.description'),
        }}
      />
    </Stack>
  )
}
