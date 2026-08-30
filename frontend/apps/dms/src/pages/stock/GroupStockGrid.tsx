import { useMemo, useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { Building2 } from 'lucide-react'
import { useTranslation } from 'react-i18next'
import { ActionBar, DataGrid, OverviewShellRegion, type SortSpec } from '@nexotec/ui-kit'
import { useUiPreferencesContext } from '../../hooks/UiPreferencesContext'
import { api } from '../../api/client'
import { toSwissLocale, type SupportedLanguage } from '../../i18n'
import { buildStockGroupColumns } from './columns/stockGroupColumns'
import type { StockItemGroupPage } from '../../api/types'

/**
 * § ADR-055 — "a different artefact, dressed differently, not a greyed-
 * out version of your own grid." Deliberately simpler chrome than the
 * own-stock grid: no saved views, no column config panel, no filters —
 * the group projection doesn't carry the fields those would operate on
 * anyway, and there is no per-user preference worth persisting for a
 * read-only cross-dealership roster.
 */
export function GroupStockGrid() {
  const { t, i18n } = useTranslation()
  const locale = toSwissLocale(i18n.language as SupportedLanguage)
  const { density, setDensity } = useUiPreferencesContext()
  const [query, setQuery] = useState('')

  const groupQuery = useQuery({
    queryKey: ['stock-items', 'group'],
    queryFn: () => api.get<StockItemGroupPage>('/inventory/groups/mine/stock-items'),
  })

  const allRows = groupQuery.data?.items ?? []
  const rows = query
    ? allRows.filter(
        (r) =>
          r.vehicleLabel.toLowerCase().includes(query.toLowerCase()) ||
          r.stockNumber.toLowerCase().includes(query.toLowerCase()) ||
          (r.vin ?? '').toLowerCase().includes(query.toLowerCase())
      )
    : allRows

  const columns = useMemo(() => buildStockGroupColumns(t, locale), [t, locale])
  const sort: SortSpec[] = []

  return (
    <OverviewShellRegion
      header={<div />}
      actionBar={
        <ActionBar
          searchValue={query}
          onSearchChange={setQuery}
          searchPlaceholder={t('stockList.searchPlaceholder')}
          density={density}
          onDensityChange={setDensity}
          onRefresh={() => groupQuery.refetch()}
          refreshing={groupQuery.isRefetching}
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
      <DataGrid
        columns={columns}
        rows={rows}
        getRowId={(row) => row.id}
        sort={sort}
        onSortChange={() => {}}
        density={density}
        loading={groupQuery.isLoading}
        refetching={groupQuery.isRefetching && !groupQuery.isLoading}
        fetchingNextPage={false}
        hasNextPage={false}
        onLoadMore={() => {}}
        error={groupQuery.isError ? 'Failed to load group stock.' : null}
        onRetry={() => groupQuery.refetch()}
        total={rows.length}
        totalIsEstimate={false}
        isFiltered={query.length > 0}
        locale={locale}
        labels={{
          showing: (count) => t('common.showing', { count }),
          showingOfTotal: (count, totalStr) => t('common.showingOfTotal', { count, total: totalStr }),
          loadingMore: t('common.loadingMore'),
          retry: t('common.retry'),
          rowActionsLabel: t('common.rowActionsLabel'),
        }}
        emptyState={{
          icon: <Building2 size={24} />,
          title: t('stockList.emptyState.title'),
          description: t('stockList.emptyState.description'),
        }}
        emptyFilteredState={{
          icon: <Building2 size={24} />,
          title: t('stockList.emptyFilteredState.title'),
          description: t('stockList.emptyFilteredState.description'),
        }}
      />
    </OverviewShellRegion>
  )
}
