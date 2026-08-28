import { useMemo, useState } from 'react'
import { Badge, Button, Group, Modal, Stack, TextInput, Title } from '@mantine/core'
import { useDebouncedValue } from '@mantine/hooks'
import { useInfiniteQuery, useQueryClient } from '@tanstack/react-query'
import { AlertTriangle, Check } from 'lucide-react'
import { useTranslation } from 'react-i18next'
import { ActionBar, DataGrid, useSetBreadcrumb, type GridColumnDef, type SortSpec } from '@nexotec/ui-kit'
import { useUiPreferencesContext } from '../hooks/UiPreferencesContext'
import { api } from '../api/client'
import type { MappingGapPage, MappingGapRead } from '../api/types'

const GRID_KEY = 'vehicleMdm.mappingGaps'

/**
 * WP-5 PR-8, FR-V-11 admin queue — every provider code ProviderCodeMap
 * couldn't resolve (app.vehicle.services.provider), never silently
 * dropped. Resolving a row here both marks it resolved and writes the
 * ProviderCodeMap row it was missing (app.vehicle.services.provider.
 * resolve_mapping_gap), so the same code never reappears as a gap.
 *
 * No server-side sort (the backend's simple cursor-paginated list
 * endpoint doesn't support it) — columns render without a sortField, so
 * the grid's header simply has no sort affordance on any of them, per
 * ADR-060/U-10's "sortability is separate from visibility" rule.
 */
export function MappingGapsPage() {
  const { t } = useTranslation()
  useSetBreadcrumb([t('shell.nav.masterData'), t('mappingGaps.title')])
  const { density, setDensity } = useUiPreferencesContext()
  const queryClient = useQueryClient()

  const [query, setQuery] = useState('')
  const [debouncedQuery] = useDebouncedValue(query, 250)
  const [sort, setSort] = useState<SortSpec[]>([])
  const [resolvingGap, setResolvingGap] = useState<MappingGapRead | null>(null)
  const [listCode, setListCode] = useState('')
  const [valueCode, setValueCode] = useState('')
  const [resolving, setResolving] = useState(false)

  const { data, fetchNextPage, hasNextPage, isFetchingNextPage, isLoading, isError, refetch, isRefetching } =
    useInfiniteQuery({
      queryKey: ['vehicle-mdm', GRID_KEY],
      queryFn: async ({ pageParam }: { pageParam: string | null }) => {
        const params = new URLSearchParams()
        params.set('resolved', 'false')
        params.set('limit', '50')
        if (pageParam) params.set('cursor', pageParam)
        return api.get<MappingGapPage>(`/vehicle-mdm/mapping-gaps?${params.toString()}`)
      },
      initialPageParam: null as string | null,
      getNextPageParam: (lastPage) => lastPage.nextCursor,
    })

  const allRows = useMemo(() => data?.pages.flatMap((page) => page.items) ?? [], [data])
  const rows = useMemo(
    () =>
      debouncedQuery
        ? allRows.filter((r) => r.providerCode.includes(debouncedQuery) || r.provider.includes(debouncedQuery))
        : allRows,
    [allRows, debouncedQuery],
  )

  const openResolve = (gap: MappingGapRead) => {
    setResolvingGap(gap)
    setListCode('')
    setValueCode('')
  }

  const submitResolve = async () => {
    if (!resolvingGap) return
    setResolving(true)
    try {
      await api.post(`/vehicle-mdm/mapping-gaps/${resolvingGap.id}/resolve`, {
        canonicalListCode: listCode,
        canonicalValueCode: valueCode,
      })
      setResolvingGap(null)
      await queryClient.invalidateQueries({ queryKey: ['vehicle-mdm', GRID_KEY] })
    } finally {
      setResolving(false)
    }
  }

  const columns: GridColumnDef<MappingGapRead>[] = useMemo(
    () => [
      { id: 'provider', header: t('mappingGaps.columns.provider'), cell: ({ row }) => row.original.provider },
      {
        id: 'vehicleKind',
        header: t('mappingGaps.columns.vehicleKind'),
        cell: ({ row }) => row.original.vehicleKind,
      },
      {
        id: 'codeGroup',
        header: t('mappingGaps.columns.codeGroup'),
        cell: ({ row }) => <span style={{ fontFamily: 'monospace' }}>{row.original.codeGroup}</span>,
      },
      {
        id: 'providerCode',
        header: t('mappingGaps.columns.providerCode'),
        cell: ({ row }) => <span style={{ fontFamily: 'monospace' }}>{row.original.providerCode}</span>,
      },
      {
        id: 'occurrences',
        header: t('mappingGaps.columns.occurrences'),
        cell: ({ row }) => <Badge variant="light">{row.original.occurrences}</Badge>,
        meta: { align: 'right' },
      },
      {
        id: 'lastSeenAt',
        header: t('mappingGaps.columns.lastSeen'),
        cell: ({ row }) => new Date(row.original.lastSeenAt).toLocaleDateString(),
        meta: { align: 'right' },
      },
      {
        id: 'resolve',
        header: '',
        cell: ({ row }) => (
          <Button size="xs" variant="light" leftSection={<Check size={14} />} onClick={() => openResolve(row.original)}>
            {t('mappingGaps.resolve')}
          </Button>
        ),
      },
    ],
    [t],
  )

  return (
    <Stack gap="md">
      <Group justify="space-between">
        <Title order={2}>{t('mappingGaps.title')}</Title>
      </Group>

      <ActionBar
        searchValue={query}
        onSearchChange={setQuery}
        searchPlaceholder={t('mappingGaps.searchPlaceholder')}
        density={density}
        onDensityChange={setDensity}
        onRefresh={() => refetch()}
        refreshing={isRefetching}
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

      <DataGrid<MappingGapRead>
        columns={columns}
        rows={rows}
        getRowId={(row) => row.id}
        sort={sort}
        onSortChange={setSort}
        density={density}
        loading={isLoading}
        fetchingNextPage={isFetchingNextPage}
        hasNextPage={Boolean(hasNextPage)}
        onLoadMore={() => fetchNextPage()}
        error={isError ? t('mappingGaps.loadError') : null}
        onRetry={() => refetch()}
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
          icon: <AlertTriangle size={24} />,
          title: t('mappingGaps.emptyState.title'),
          description: t('mappingGaps.emptyState.description'),
        }}
        emptyFilteredState={{
          icon: <AlertTriangle size={24} />,
          title: t('mappingGaps.emptyFilteredState.title'),
          description: t('mappingGaps.emptyFilteredState.description'),
          action: (
            <Button variant="default" onClick={() => setQuery('')}>
              {t('mappingGaps.emptyFilteredState.action')}
            </Button>
          ),
        }}
      />

      <Modal opened={resolvingGap !== null} onClose={() => setResolvingGap(null)} title={t('mappingGaps.resolveModal.title')}>
        <Stack gap="sm">
          <TextInput
            label={t('mappingGaps.resolveModal.listCode')}
            value={listCode}
            onChange={(e) => setListCode(e.currentTarget.value)}
            data-autofocus
          />
          <TextInput
            label={t('mappingGaps.resolveModal.valueCode')}
            value={valueCode}
            onChange={(e) => setValueCode(e.currentTarget.value)}
          />
          <Group justify="flex-end">
            <Button variant="default" onClick={() => setResolvingGap(null)}>
              {t('common.cancel')}
            </Button>
            <Button onClick={() => void submitResolve()} loading={resolving} disabled={!listCode || !valueCode}>
              {t('mappingGaps.resolveModal.confirm')}
            </Button>
          </Group>
        </Stack>
      </Modal>
    </Stack>
  )
}
