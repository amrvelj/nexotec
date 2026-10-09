import { useEffect, useMemo, useState } from 'react'
import { useSearchParams } from 'react-router-dom'
import { Alert, Badge, Button, Select, Stack, TextInput } from '@mantine/core'
import { useDebouncedValue } from '@mantine/hooks'
import { useInfiniteQuery, useQueryClient } from '@tanstack/react-query'
import { AlertTriangle, Check } from 'lucide-react'
import { useTranslation } from 'react-i18next'
import { ActionBar, DataGrid, FormDialog, type GridColumnDef, type SortSpec } from '@nexotec/ui-kit'
import { useUiPreferencesContext } from '../hooks/UiPreferencesContext'
import { useReferenceValueOptions } from '../hooks/useReferenceValueOptions'
import { api, ApiError } from '../api/client'
import { useIdempotencyKey } from '../hooks/useIdempotencyKey'
import type { MappingGapPage, MappingGapRead } from '../api/types'
import { toSwissLocale, type SupportedLanguage } from '../i18n'
import { formatDate, formatNumber } from '../utils/format'

const GRID_KEY = 'vehicleMdm.mappingGaps'

interface MappingGapsQueueProps {
  /**
   * Prefix for this queue's URL params, so it can live standalone at
   * `/vehicle-mdm/mapping-gaps` (`?q=`) AND as a section of
   * `/settings/reference` (`?gapsQ=`) without the two grids fighting over
   * one `q` param. ADR-056 — grid state in the URL, per grid.
   */
  paramPrefix?: string
}

/**
 * WP-5 PR-8 / FR-V-11 admin queue — every provider code ProviderCodeMap
 * couldn't resolve (app.vehicle.services.provider), never silently
 * dropped. Resolving a row here both marks it resolved and writes the
 * ProviderCodeMap row it was missing, so the same code never reappears.
 *
 * Presentational: no breadcrumb, no page title — the host screen owns
 * those. Reused by MappingGapsPage and ReferenceDataPage (do not fork).
 *
 * No server-side sort (the list endpoint doesn't support it) — columns
 * render without a sortField, so the header has no sort affordance, per
 * ADR-060 / U-10's "sortability is separate from visibility".
 *
 * The resolve dialog (KAN-77) offers only the active values of the gap's own
 * reference list — its `codeGroup` is that list's code — so a typo can never
 * become a canonical value. A refused resolve (409 already mapped / no list,
 * 422 not an active value) shows the server's message in the dialog.
 */
export function MappingGapsQueue({ paramPrefix = '' }: MappingGapsQueueProps) {
  const { t, i18n } = useTranslation()
  const locale = toSwissLocale(i18n.language as SupportedLanguage)
  const { density, setDensity } = useUiPreferencesContext()
  const queryClient = useQueryClient()
  const [searchParams, setSearchParams] = useSearchParams()

  const qParam = `${paramPrefix}q`
  const [query, setQuery] = useState(() => searchParams.get(qParam) ?? '')
  const [debouncedQuery] = useDebouncedValue(query, 250)
  useEffect(() => {
    setSearchParams(
      (prev) => {
        const next = new URLSearchParams(prev)
        if (debouncedQuery) next.set(qParam, debouncedQuery)
        else next.delete(qParam)
        return next
      },
      { replace: true },
    )
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [debouncedQuery])

  const [sort, setSort] = useState<SortSpec[]>([])
  const [resolvingGap, setResolvingGap] = useState<MappingGapRead | null>(null)
  const [valueCode, setValueCode] = useState<string | null>(null)
  const [resolving, setResolving] = useState(false)
  const [resolveError, setResolveError] = useState<string | null>(null)
  const valueOptions = useReferenceValueOptions(resolvingGap?.codeGroup ?? null)
  // KAN-266: one key per resolution — the same gap and value sent again (a
  // retry, from the same dialog or a reopened one) replays the first answer;
  // another gap or value gets a new key.
  const resolveKey = useIdempotencyKey()

  const openResolve = (gap: MappingGapRead) => {
    setResolvingGap(gap)
    setValueCode(null)
    setResolveError(null)
  }

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
        ? allRows.filter(
            (r) => r.providerCode.includes(debouncedQuery) || r.provider.includes(debouncedQuery),
          )
        : allRows,
    [allRows, debouncedQuery],
  )

  const submitResolve = async () => {
    if (!resolvingGap || !valueCode) return
    setResolving(true)
    setResolveError(null)
    try {
      const path = `/vehicle-mdm/mapping-gaps/${resolvingGap.id}/resolve`
      const body = { canonicalListCode: resolvingGap.codeGroup, canonicalValueCode: valueCode }
      await api.post(path, body, resolveKey.headers([path, body]))
      resolveKey.renew()
      setResolvingGap(null)
      await queryClient.invalidateQueries({ queryKey: ['vehicle-mdm', GRID_KEY] })
    } catch (err) {
      setResolveError(err instanceof ApiError ? err.message : t('mappingGaps.resolveModal.error'))
    } finally {
      setResolving(false)
    }
  }

  const columns: GridColumnDef<MappingGapRead>[] = useMemo(
    () => [
      { id: 'provider', header: t('mappingGaps.columns.provider'), cell: ({ row }) => row.original.provider },
      { id: 'vehicleKind', header: t('mappingGaps.columns.vehicleKind'), cell: ({ row }) => row.original.vehicleKind },
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
        cell: ({ row }) => <Badge variant="light">{formatNumber(row.original.occurrences)}</Badge>,
        meta: { align: 'right' },
      },
      {
        id: 'lastSeenAt',
        header: t('mappingGaps.columns.lastSeen'),
        cell: ({ row }) => formatDate(row.original.lastSeenAt, locale),
        meta: { align: 'right' },
      },
      {
        id: 'resolve',
        header: '',
        cell: ({ row }) => (
          <Button
            size="xs"
            variant="light"
            leftSection={<Check size={14} />}
            onClick={() => openResolve(row.original)}
          >
            {t('mappingGaps.resolve')}
          </Button>
        ),
      },
    ],
    [t, locale],
  )

  return (
    <Stack gap="md">
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

      <FormDialog
        opened={resolvingGap !== null}
        onClose={() => setResolvingGap(null)}
        title={t('mappingGaps.resolveModal.title')}
        onSubmit={submitResolve}
        submitLabel={t('mappingGaps.resolveModal.confirm')}
        cancelLabel={t('common.cancel')}
        submitting={resolving}
        submitDisabled={!valueCode}
      >
        <TextInput
          label={t('mappingGaps.resolveModal.gapKey')}
          value={resolvingGap ? `${resolvingGap.provider} · ${resolvingGap.vehicleKind} · ${resolvingGap.providerCode}` : ''}
          readOnly
        />
        <TextInput label={t('mappingGaps.resolveModal.listCode')} value={resolvingGap?.codeGroup ?? ''} readOnly />
        <Select
          label={t('mappingGaps.resolveModal.valueCode')}
          placeholder={t('mappingGaps.resolveModal.valuePlaceholder')}
          data={valueOptions.options}
          value={valueCode}
          onChange={(value) => {
            setValueCode(value)
            setResolveError(null)
          }}
          searchable
          nothingFoundMessage={t('mappingGaps.resolveModal.noValues')}
          disabled={valueOptions.isLoading || valueOptions.isError}
          error={
            valueOptions.isMissingList
              ? t('mappingGaps.resolveModal.noList', { codeGroup: resolvingGap?.codeGroup ?? '' })
              : valueOptions.isError
                ? t('mappingGaps.resolveModal.valuesLoadError')
                : undefined
          }
          comboboxProps={{ withinPortal: true }}
          data-autofocus
        />
        {resolveError && (
          <Alert color="red" role="alert">
            {resolveError}
          </Alert>
        )}
      </FormDialog>
    </Stack>
  )
}
