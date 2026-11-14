import { useMemo } from 'react'
import { Card, Group, Stack, Text, Title } from '@mantine/core'
import { useQuery } from '@tanstack/react-query'
import { useTranslation } from 'react-i18next'
import { ColumnConfigPanel, DataGrid, defaultColumnLayout, formatNumber, slate, sortRowsInBrowser, type GridColumnDef } from '@nexotec/ui-kit'
import { api } from '../../api/client'
import type { MissingTranslationPage, MissingTranslationRead } from '../../api/types'
import { useColumnPanelLabels } from '../../hooks/useColumnPanelLabels'
import { useDataGridLabels } from '../../hooks/useDataGridLabels'
import { useGridPreferences } from '../../hooks/useGridPreferences'
import { useSortInUrl } from '../../hooks/useSortInUrl'
import { useUiPreferencesContext } from '../../hooks/UiPreferencesContext'
import { columnRegistryOf } from '../../utils/columnRegistry'
import { formatDateTime } from '../../utils/format'
import { SUPPORTED_LANGUAGES, toSwissLocale, type SupportedLanguage } from '../../i18n'

const GRID_KEY = 'platform.translations.missing'

/** KAN-709 — the one page the log is read in: the endpoint answers the latest
 * reports first and has no cursor (KAN-209), so the card asks for exactly this
 * many and says so whenever the answer is full: a browser sort then orders
 * only these, not the whole log. */
export const MISSING_LOG_LIMIT = 100

/** KAN-209 — the `missing_translation` log (FR-A-30: "reported, never
 * rendered blank"): keys a browser rendered as the loud marker.
 *
 * KAN-516 (Anto, 2026-11-13, option C): the log is the kit's grid with the
 * Columns panel (ADR-060), sorted in the browser — the endpoint answers the
 * latest reports first, at most one page, and takes no sort. Its sort is in
 * the URL as `missingSort`, beside the UI strings' own `sort`. An entry has
 * nothing to be done to it (it is a report, read here), so its rows carry
 * no row menu. */
export function MissingTranslationsCard() {
  const { t, i18n } = useTranslation()
  const { density } = useUiPreferencesContext()
  const labels = useDataGridLabels()
  const columnPanelLabels = useColumnPanelLabels()
  const gridPrefs = useGridPreferences(GRID_KEY, { sort: [] })
  const [sort, setSort] = useSortInUrl(gridPrefs, 'missingSort')
  const missing = useQuery({
    queryKey: ['ui-translations', 'missing'],
    queryFn: () => api.get<MissingTranslationPage>(`/platform/ui-translations/missing?limit=${MISSING_LOG_LIMIT}`),
  })
  const locale = toSwissLocale((SUPPORTED_LANGUAGES as readonly string[]).includes(i18n.language) ? (i18n.language as SupportedLanguage) : 'de')
  const items = useMemo(() => missing.data?.items ?? [], [missing.data])

  const columns: GridColumnDef<MissingTranslationRead>[] = useMemo(
    () => [
      {
        id: 'key',
        header: t('translations.missing.columns.key'),
        cell: ({ row }) => row.original.key,
        meta: { locked: true, mono: true, sortField: 'key', sortValue: (item) => item.key },
      },
      {
        id: 'language',
        header: t('translations.missing.columns.language'),
        cell: ({ row }) => t(`translations.languages.${row.original.language}`),
        meta: { sortField: 'language', sortValue: (item) => t(`translations.languages.${item.language}`) },
      },
      {
        id: 'count',
        header: t('translations.missing.columns.count'),
        cell: ({ row }) => formatNumber(row.original.reportCount),
        meta: { align: 'right', sortField: 'count', sortValue: (item) => item.reportCount },
      },
      {
        id: 'first',
        header: t('translations.missing.columns.first'),
        cell: ({ row }) => formatDateTime(row.original.firstReportedAt, locale),
        meta: { align: 'right', defaultVisible: false, sortField: 'first', sortValue: (item) => item.firstReportedAt },
      },
      {
        id: 'last',
        header: t('translations.missing.columns.last'),
        cell: ({ row }) => formatDateTime(row.original.lastReportedAt, locale),
        meta: { align: 'right', sortField: 'last', sortValue: (item) => item.lastReportedAt },
      },
    ],
    [t, locale],
  )
  const columnRegistry = useMemo(() => columnRegistryOf(columns), [columns])
  const columnLayout = useMemo(() => gridPrefs.columnLayout ?? defaultColumnLayout(columnRegistry), [gridPrefs.columnLayout, columnRegistry])
  const rows = useMemo(() => sortRowsInBrowser(items, sort, columns, locale), [items, sort, columns, locale])

  return (
    <Card withBorder padding="lg" radius="md">
      <Stack gap="sm">
        <Group justify="space-between" align="flex-start" wrap="nowrap">
          <Stack gap={2}>
            <Title order={4}>{t('translations.missing.title')}</Title>
            <Text size="sm" c={slate[5]}>
              {t('translations.missing.subtitle')}
            </Text>
          </Stack>
          <ColumnConfigPanel registry={columnRegistry} layout={columnLayout} onLayoutChange={gridPrefs.setColumnLayout} labels={columnPanelLabels} />
        </Group>
        {items.length >= MISSING_LOG_LIMIT && (
          <Text size="sm" c={slate[5]} data-testid="missing-latest-only">
            {t('translations.missing.latestOnly', { limit: MISSING_LOG_LIMIT })}
          </Text>
        )}
        {missing.data && items.length === 0 && (
          <Text size="sm" c={slate[5]}>
            {t('translations.missing.empty')}
          </Text>
        )}
        {items.length > 0 && (
          <DataGrid<MissingTranslationRead>
            ariaLabel={t('translations.missing.gridLabel')}
            columns={columns}
            rows={rows}
            getRowId={(item) => `${item.language}:${item.key}`}
            sort={sort}
            onSortChange={setSort}
            density={density}
            loading={false}
            fetchingNextPage={false}
            hasNextPage={false}
            onLoadMore={() => {}}
            total={rows.length}
            totalIsEstimate={false}
            isFiltered={false}
            labels={labels}
            emptyState={{ icon: null, title: t('translations.missing.empty'), description: '' }}
            columnLayout={columnLayout}
            onColumnLayoutChange={gridPrefs.setColumnLayout}
          />
        )}
      </Stack>
    </Card>
  )
}
