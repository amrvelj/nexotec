import { useMemo, useState } from 'react'
import { Alert, Button, Group, Paper, Skeleton, Stack, Tabs, Text, Title } from '@mantine/core'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import { Eye, Pencil } from 'lucide-react'
import { useTranslation } from 'react-i18next'
import { useSearchParams } from 'react-router-dom'
import {
  Badge,
  ColumnConfigPanel,
  DataGrid,
  LanguageBadge,
  defaultColumnLayout,
  sortRowsInBrowser,
  useSetBreadcrumb,
  type GridColumnDef,
  type RowMenuGroups,
} from '@nexotec/ui-kit'
import { api } from '../api/client'
import type { StandardTextCategory, StandardTextList, StandardTextRegistryRead } from '../api/types'
import { useAuth } from '../auth/AuthContext'
import { StandardTextEditor } from '../components/standardTexts/StandardTextEditor'
import { TEXT_LANGUAGES, effective, entryKind, entryTitle, groupTexts, seedChanged, type TextEntry } from '../components/standardTexts/standardTextModel'
import { useColumnPanelLabels } from '../hooks/useColumnPanelLabels'
import { useDataGridLabels } from '../hooks/useDataGridLabels'
import { useGridPreferences } from '../hooks/useGridPreferences'
import { useSortInUrl } from '../hooks/useSortInUrl'
import { useUiPreferencesContext } from '../hooks/UiPreferencesContext'
import { SUPPORTED_LANGUAGES, toSwissLocale, type SupportedLanguage } from '../i18n'
import { TEXT_CATEGORY_VALUES } from '../userOptions'
import { columnRegistryOf } from '../utils/columnRegistry'
import { formatDateTime } from '../utils/format'

const standardTextsKey = (dealershipId: string) => ['standard-texts', dealershipId]

const GRID_KEY = 'administration.standardTexts.list'

/** The latest change to any language of the entry — the Betrieb's row where
 * it has one, else the seed's. */
function lastChanged(entry: TextEntry): string | null {
  const stamps = TEXT_LANGUAGES.map((language) => effective(entry, language)?.updatedAt).filter((stamp): stamp is string => Boolean(stamp))
  return stamps.length > 0 ? stamps.reduce((latest, stamp) => (stamp > latest ? stamp : latest)) : null
}

/**
 * Dealer Administration › Standard texts (KAN-208; PRD "Standard texts";
 * A-RULE-0: the one place a Betrieb's wording is configured). The library
 * by category — the seeded templates labelled «Vorlage — bitte prüfen»
 * (FR-A-08), the Betrieb's adaptations and its own texts — and one editor
 * with language tabs, the target's placeholders only and a preview on sample
 * data (FR-A-07). Any role reads; a category is written by the manager or by
 * a user holding its delegation (FR-A-28): the library says which
 * (`writableCategories`), and the editor offers no write beyond it — the
 * server's gate stays the authority. The category tab and the sort live in
 * the URL (ADR-056).
 *
 * KAN-516 (Anto, 2026-11-13, option C): the library is the kit's grid — the
 * row menu opens a text (ADR-061), the Columns panel is the user's
 * (ADR-060) — and, being a short fixed list the endpoint does not sort, it
 * sorts in the browser (`sortRowsInBrowser`); no sort keeps the library's
 * own order (use, position, code).
 */
export function StandardTextsPage() {
  const { t, i18n } = useTranslation()
  useSetBreadcrumb([t('shell.nav.administration'), t('standardTexts.title')])
  const { activeDealership } = useAuth()
  const dealershipId = activeDealership?.id ?? ''
  const queryClient = useQueryClient()
  const { density } = useUiPreferencesContext()
  const labels = useDataGridLabels()
  const columnPanelLabels = useColumnPanelLabels()
  const gridPrefs = useGridPreferences(GRID_KEY, { sort: [] })
  const [sort, setSort] = useSortInUrl(gridPrefs)
  const [params, setParams] = useSearchParams()
  const category = (TEXT_CATEGORY_VALUES as string[]).includes(params.get('category') ?? '')
    ? (params.get('category') as StandardTextCategory)
    : 'document'
  const uiLanguage: SupportedLanguage = (SUPPORTED_LANGUAGES as readonly string[]).includes(i18n.language)
    ? (i18n.language as SupportedLanguage)
    : 'de'
  const locale = toSwissLocale(uiLanguage)
  const [editing, setEditing] = useState<{ code: string | null } | null>(null)

  const library = useQuery({
    queryKey: standardTextsKey(dealershipId),
    queryFn: () => api.get<StandardTextList>(`/dealerships/${dealershipId}/standard-texts`),
    enabled: dealershipId !== '',
  })
  const registry = useQuery({
    queryKey: ['standard-text-registry'],
    queryFn: () => api.get<StandardTextRegistryRead>('/standard-text-registry'),
    staleTime: Infinity,
  })

  const entries = useMemo(() => groupTexts(library.data?.items ?? []), [library.data])
  const writable = new Set(library.data?.writableCategories ?? [])
  const categoryWritable = writable.has(category)
  const targets = (registry.data?.targets ?? []).filter((target) => target.category === category)
  const inCategory = useMemo(() => entries.filter((entry) => entry.category === category), [entries, category])
  const editingEntry = editing?.code ? (entries.find((entry) => entry.code === editing.code) ?? null) : null

  const countOf = (value: StandardTextCategory) => entries.filter((entry) => entry.category === value).length

  const columns: GridColumnDef<TextEntry>[] = useMemo(
    () => [
      {
        id: 'title',
        header: t('standardTexts.columns.title'),
        cell: ({ row }) => entryTitle(row.original, uiLanguage),
        meta: {
          locked: true,
          sortField: 'title',
          sortValue: (entry) => entryTitle(entry, uiLanguage),
          secondary: (entry) => (effective(entry, uiLanguage)?.active === false ? t('standardTexts.inactive') : null),
        },
      },
      {
        id: 'code',
        header: t('standardTexts.columns.code'),
        cell: ({ row }) => row.original.code,
        meta: { mono: true, defaultVisible: false, sortField: 'code', sortValue: (entry) => entry.code },
      },
      {
        id: 'appliesTo',
        header: t('standardTexts.columns.appliesTo'),
        cell: ({ row }) => t(`standardTexts.appliesTo.${row.original.appliesTo}`),
        meta: { sortField: 'appliesTo', sortValue: (entry) => t(`standardTexts.appliesTo.${entry.appliesTo}`) },
      },
      {
        id: 'slot',
        header: t('standardTexts.columns.slot'),
        cell: ({ row }) => t(`standardTexts.slot.${row.original.slot}`),
        meta: { sortField: 'slot', sortValue: (entry) => t(`standardTexts.slot.${entry.slot}`) },
      },
      {
        id: 'source',
        header: t('standardTexts.columns.source'),
        cell: ({ row }) => {
          const kind = entryKind(row.original)
          return (
            <Group gap={6} wrap="nowrap">
              <Badge tone={kind === 'template' ? 'warning' : kind === 'adapted' ? 'purple' : 'slate'}>{t(`standardTexts.badge.${kind}`)}</Badge>
              {seedChanged(row.original) && (
                <Badge tone="destructive" dot>
                  {t('standardTexts.badge.seedChanged')}
                </Badge>
              )}
            </Group>
          )
        },
        meta: { sortField: 'source', sortValue: (entry) => t(`standardTexts.badge.${entryKind(entry)}`) },
      },
      {
        id: 'languages',
        header: t('standardTexts.columns.languages'),
        cell: ({ row }) => (
          <Group gap={6} wrap="nowrap">
            {TEXT_LANGUAGES.filter((language) => effective(row.original, language) !== null).map((language) => (
              <LanguageBadge key={language} language={language} />
            ))}
          </Group>
        ),
        meta: {
          sortField: 'languages',
          sortValue: (entry) => TEXT_LANGUAGES.filter((language) => effective(entry, language) !== null).length,
        },
      },
      {
        id: 'default',
        header: t('standardTexts.columns.default'),
        cell: ({ row }) => (effective(row.original, uiLanguage)?.isDefault ? t('common.yes') : '—'),
        meta: { sortField: 'default', sortValue: (entry) => effective(entry, uiLanguage)?.isDefault === true },
      },
      {
        id: 'updatedAt',
        header: t('standardTexts.columns.updatedAt'),
        cell: ({ row }) => {
          const stamp = lastChanged(row.original)
          return stamp ? formatDateTime(stamp, locale) : '—'
        },
        meta: { align: 'right', defaultVisible: false, sortField: 'updatedAt', sortValue: lastChanged },
      },
    ],
    [t, uiLanguage, locale],
  )
  const columnRegistry = useMemo(() => columnRegistryOf(columns), [columns])
  const columnLayout = useMemo(() => gridPrefs.columnLayout ?? defaultColumnLayout(columnRegistry), [gridPrefs.columnLayout, columnRegistry])
  const rows = useMemo(() => sortRowsInBrowser(inCategory, sort, columns, locale), [inCategory, sort, columns, locale])

  // ADR-061: the one definition of what can be done to a text from the list.
  const rowMenu = (entry: TextEntry): RowMenuGroups => ({
    edit: [
      categoryWritable
        ? { label: t('standardTexts.edit'), icon: <Pencil size={16} />, onClick: () => setEditing({ code: entry.code }) }
        : { label: t('standardTexts.view'), icon: <Eye size={16} />, onClick: () => setEditing({ code: entry.code }) },
    ],
  })

  const chooseCategory = (value: string | null) => {
    if (!value) return
    setParams(
      (prev) => {
        const next = new URLSearchParams(prev)
        next.set('category', value)
        return next
      },
      { replace: true },
    )
  }

  return (
    <Stack gap="md">
      <Group justify="space-between" align="flex-start">
        <div>
          <Title order={2}>{t('standardTexts.title')}</Title>
          <Text c="dimmed" fz="sm">
            {t('standardTexts.subtitle')}
          </Text>
        </div>
        <Group gap="sm">
          <ColumnConfigPanel registry={columnRegistry} layout={columnLayout} onLayoutChange={gridPrefs.setColumnLayout} labels={columnPanelLabels} />
          {categoryWritable && targets.length > 0 && <Button onClick={() => setEditing({ code: null })}>{t('standardTexts.newText')}</Button>}
        </Group>
      </Group>
      <Alert color="yellow" variant="light">
        {t('standardTexts.disclaimer')}
      </Alert>

      <Tabs value={category} onChange={chooseCategory}>
        <Tabs.List>
          {TEXT_CATEGORY_VALUES.map((value) => (
            <Tabs.Tab key={value} value={value}>
              {t(`standardTexts.category.${value}`)} ({countOf(value)})
            </Tabs.Tab>
          ))}
        </Tabs.List>
      </Tabs>

      {(library.isError || registry.isError) && (
        <Alert color="red" role="alert">
          {t('standardTexts.loadError')}
        </Alert>
      )}
      {library.isLoading && <Skeleton height={240} radius="md" />}
      {library.data && !categoryWritable && (
        <Text c="dimmed" fz="sm">
          {t('standardTexts.readOnly')}
        </Text>
      )}
      {library.data && inCategory.length === 0 && (
        <Paper withBorder p="lg" radius="md">
          <Text c="dimmed">{t('standardTexts.empty')}</Text>
        </Paper>
      )}
      {inCategory.length > 0 && (
        <DataGrid<TextEntry>
          ariaLabel={t(`standardTexts.category.${category}`)}
          columns={columns}
          rows={rows}
          getRowId={(entry) => entry.code}
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
          emptyState={{ icon: null, title: t('standardTexts.empty'), description: '' }}
          rowActions={rowMenu}
          columnLayout={columnLayout}
          onColumnLayoutChange={gridPrefs.setColumnLayout}
        />
      )}

      {editing && registry.data && (
        <StandardTextEditor
          key={editing.code ?? 'new'}
          opened
          onClose={() => setEditing(null)}
          dealershipId={dealershipId}
          category={category}
          entry={editingEntry}
          targets={targets}
          writable={categoryWritable}
          uiLanguage={uiLanguage}
          onSaved={() => void queryClient.invalidateQueries({ queryKey: standardTextsKey(dealershipId) })}
        />
      )}
    </Stack>
  )
}
