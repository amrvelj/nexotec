import { useMemo, useState } from 'react'
import { Stack, Title } from '@mantine/core'
import { useDebouncedValue } from '@mantine/hooks'
import { useInfiniteQuery, useQuery, useQueryClient } from '@tanstack/react-query'
import { Building2 } from 'lucide-react'
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
import { DeleteConnectionModal } from '../components/DeleteConnectionModal'
import { translatedConnectionStatusLabel } from '../integrationOptions'
import { useIdempotencyKey } from '../hooks/useIdempotencyKey'
import { useSavedViews } from '../hooks/useSavedViews'
import { useViewsAndFiltersLabels } from '../hooks/useViewsAndFiltersLabels'
import { useUiPreferencesContext } from '../hooks/UiPreferencesContext'
import { toSwissLocale, type SupportedLanguage } from '../i18n'
import { formatDate } from '../utils/format'
import type {
  DealershipPage,
  IntegrationConnectionPage,
  IntegrationConnectionRead,
  IntegrationProviderPage,
} from '../api/types'
import { useActionBarLabels } from '../hooks/useActionBarLabels'
import { useDataGridLabels } from '../hooks/useDataGridLabels'

const CONNECTIONS_GRID_KEY = 'integrations.platform.connections'

/**
 * WP-6 PR-7 — platform view: every connection across every tenant
 * (filterable), a `DataGrid`. Composed from app.integration's own connection
 * list joined client-side against the dealership list (for display names) —
 * never a cross-context reach-in on either backend. The fleet-wide catalogue
 * sync board that stood below it was retired by KAN-497 with the full
 * catalogue download it reported on (KAN-485).
 */
export function IntegrationPlatformView() {
  const { t, i18n } = useTranslation()
  const dataGridLabels = useDataGridLabels()
  const viewsAndFiltersLabels = useViewsAndFiltersLabels()
  const actionBarLabels = useActionBarLabels()
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

  // KAN-603 — the provider's name for the delete confirmation.
  const providersQuery = useQuery({
    queryKey: ['integrations', 'providers'],
    queryFn: () => api.get<IntegrationProviderPage>('/integrations/providers'),
  })
  const providerNameById = useMemo(
    () => new Map((providersQuery.data?.items ?? []).map((p) => [p.id, p.displayName] as const)),
    [providersQuery.data]
  )
  const [deletingFor, setDeletingFor] = useState<IntegrationConnectionRead | null>(null)

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
  // KAN-266 — as on the dealer view: one key per kind, passed the request,
  // renewed on success. Enable and disable require If-Match.
  const testKey = useIdempotencyKey()
  const toggleKey = useIdempotencyKey()
  const test = async (connection: IntegrationConnectionRead) => {
    const path = `/integrations/connections/${connection.id}/test`
    await api.post(path, undefined, testKey.headers([path]))
    testKey.renew()
    await invalidate()
  }
  const toggleEnabled = async (connection: IntegrationConnectionRead) => {
    const path = `/integrations/connections/${connection.id}/${connection.enabled ? 'disable' : 'enable'}`
    await api.post(path, undefined, { 'If-Match': String(connection.version), ...toggleKey.headers([path]) })
    toggleKey.renew()
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
      { id: 'status', header: t('integrationsList.columns.status'), cell: ({ row }) => <ConnectionStatusBadge status={row.original.status} label={translatedConnectionStatusLabel(t, row.original.status)} /> },
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
              labels={actionBarLabels}
              searchValue={query}
              onSearchChange={setQuery}
              searchPlaceholder={t('integrationsList.searchPlaceholder')}
              density={density}
              onDensityChange={setDensity}
              onRefresh={() => connectionsQuery.refetch()}
              refreshing={connectionsQuery.isRefetching}
              filterSlot={
                <ViewsAndFilters
                  labels={viewsAndFiltersLabels}
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
            labels={dataGridLabels}
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
                onDelete: () => setDeletingFor(row),
              })
            }
          />
        </OverviewShellRegion>
      </Stack>
      <DeleteConnectionModal
        connection={deletingFor}
        providerName={deletingFor ? providerNameById.get(deletingFor.providerId) ?? deletingFor.providerCode : ''}
        onClose={() => setDeletingFor(null)}
        onDeleted={() => void invalidate()}
      />

    </Stack>
  )
}
