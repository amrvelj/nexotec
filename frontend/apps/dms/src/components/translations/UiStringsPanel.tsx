import { useEffect, useMemo, useState } from 'react'
import { useSearchParams } from 'react-router-dom'
import { Alert, Badge, Card, Checkbox, Group, Select, Stack, Text, TextInput } from '@mantine/core'
import { useDebouncedValue } from '@mantine/hooks'
import { useInfiniteQuery, useQuery, useQueryClient } from '@tanstack/react-query'
import { Eye, Pencil, Search } from 'lucide-react'
import { useTranslation } from 'react-i18next'
import {
  ColumnConfigPanel,
  DataGrid,
  defaultColumnLayout,
  slate,
  sortRowsInBrowser,
  type GridColumnDef,
  type RowMenuGroups,
} from '@nexotec/ui-kit'
import { api } from '../../api/client'
import type { UiTranslationKeyPage, UiTranslationKeyRead, UiTranslationNamespaces, UiTranslationRead } from '../../api/types'
import { useCanWrite } from '../../auth/useCanWrite'
import { SUPPORTED_LANGUAGES, toSwissLocale, type SupportedLanguage } from '../../i18n'
import { reloadPublishedTranslations } from '../../i18n/runtimeTranslations'
import { useColumnPanelLabels } from '../../hooks/useColumnPanelLabels'
import { useDataGridLabels } from '../../hooks/useDataGridLabels'
import { useGridPreferences } from '../../hooks/useGridPreferences'
import { useIdempotencyKey } from '../../hooks/useIdempotencyKey'
import { useSortInUrl } from '../../hooks/useSortInUrl'
import { useUiPreferencesContext } from '../../hooks/UiPreferencesContext'
import { apiRefusalText } from '../../utils/apiRefusal'
import { columnRegistryOf } from '../../utils/columnRegistry'
import { formatDateTime } from '../../utils/format'
import { FourLanguageEditor, type EditorLanguageRow, type PublishOutcome } from './FourLanguageEditor'
import { MissingTranslationsCard } from './MissingTranslationsCard'

// The endpoint's largest page (`pagination_max_limit`): the grid loads the
// next one as the reader scrolls near the end, and a sort loads them all.
const PAGE_SIZE = 100
const BASE = '/platform/ui-translations'
const GRID_KEY = 'platform.translations.uiStrings'

/** The latest change to any language of the key. */
function lastChanged(item: UiTranslationKeyRead): string | null {
  const stamps = SUPPORTED_LANGUAGES.map((language) => item[language]?.updatedAt).filter((stamp): stamp is string => Boolean(stamp))
  return stamps.length > 0 ? stamps.reduce((latest, stamp) => (stamp > latest ? stamp : latest)) : null
}

function keyPath(item: { namespace: string; key: string }): string {
  return `${item.namespace}.${item.key}`
}

function editorRows(item: UiTranslationKeyRead): Partial<Record<SupportedLanguage, EditorLanguageRow>> {
  const rows: Partial<Record<SupportedLanguage, EditorLanguageRow>> = {}
  for (const language of SUPPORTED_LANGUAGES) {
    const row = item[language]
    if (row) rows[language] = { value: row.value, seedValue: row.seedValue, edited: row.source === 'platform', version: row.version }
  }
  return rows
}

function rowPath(row: { namespace: string; key: string; language: string }): string {
  return `${BASE}/${encodeURIComponent(row.namespace)}/${encodeURIComponent(row.key)}/${row.language}`
}

/**
 * KAN-209 — the UI-strings source of the Translations panel (FR-A-30…32):
 * namespace/key search over every key the bundles shipped, the four-column
 * editor, the diff against the shipped text, publish and revert. Every write
 * is platform staff's at the API; anyone else reads (KAN-430: the actions
 * are hidden, never the data). Search, area, «edited only», the sort and the
 * open key live in the URL (ADR-056).
 *
 * KAN-516 (Anto, 2026-11-13, option C): the keys are the kit's grid — the row
 * menu opens a key (ADR-061), the Columns panel is the user's (ADR-060), the
 * next page loads as the reader scrolls. The endpoint takes no sort, so the
 * grid sorts in the browser — and a sort orders the whole result, never the
 * part loaded so far: while one is set, the remaining pages are loaded too
 * (the search narrows them; the whole catalogue is some 3'000 keys). No sort
 * keeps the endpoint's order (area, then key).
 */
export function UiStringsPanel() {
  const { t, i18n } = useTranslation()
  const locale = toSwissLocale((SUPPORTED_LANGUAGES as readonly string[]).includes(i18n.language) ? (i18n.language as SupportedLanguage) : 'de')
  const { density } = useUiPreferencesContext()
  const labels = useDataGridLabels()
  const columnPanelLabels = useColumnPanelLabels()
  const gridPrefs = useGridPreferences(GRID_KEY, { sort: [] })
  const [sort, setSort] = useSortInUrl(gridPrefs)
  const canWrite = useCanWrite('ui_translations')
  const queryClient = useQueryClient()
  const revertKey = useIdempotencyKey()
  const [searchParams, setSearchParams] = useSearchParams()
  const namespace = searchParams.get('ns') ?? ''
  const editedOnly = searchParams.get('edited') === '1'
  const openKey = searchParams.get('key')
  const [query, setQuery] = useState(searchParams.get('q') ?? '')
  const [debouncedQuery] = useDebouncedValue(query.trim(), 250)

  const updateUrl = (patch: Record<string, string | null>) => {
    setSearchParams(
      (prev) => {
        const next = new URLSearchParams(prev)
        for (const [name, value] of Object.entries(patch)) {
          if (value === null || value === '') next.delete(name)
          else next.set(name, value)
        }
        return next
      },
      { replace: true },
    )
  }

  useEffect(() => {
    if ((searchParams.get('q') ?? '') !== debouncedQuery) updateUrl({ q: debouncedQuery || null })
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [debouncedQuery])

  const listKey = ['ui-translations', debouncedQuery, namespace, editedOnly] as const
  const list = useInfiniteQuery({
    queryKey: listKey,
    initialPageParam: null as string | null,
    queryFn: ({ pageParam }) => {
      const params = new URLSearchParams({ limit: String(PAGE_SIZE) })
      if (debouncedQuery) params.set('q', debouncedQuery)
      if (namespace) params.set('namespace', namespace)
      if (editedOnly) params.set('edited', 'true')
      if (pageParam) params.set('cursor', pageParam)
      return api.get<UiTranslationKeyPage>(`${BASE}?${params}`)
    },
    getNextPageParam: (last) => last.nextCursor ?? null,
  })
  const namespaces = useQuery({
    queryKey: ['ui-translations', 'namespaces'],
    queryFn: () => api.get<UiTranslationNamespaces>(`${BASE}/namespaces`),
  })

  const items = useMemo(() => list.data?.pages.flatMap((page) => page.items) ?? [], [list.data])
  const firstPage = list.data?.pages[0]
  const selected = useMemo(() => items.find((item) => keyPath(item) === openKey) ?? null, [items, openKey])
  const rows = useMemo(() => (selected ? editorRows(selected) : {}), [selected])

  const columns: GridColumnDef<UiTranslationKeyRead>[] = useMemo(
    () => [
      {
        id: 'key',
        header: t('translations.columns.key'),
        cell: ({ row }) => keyPath(row.original),
        meta: { locked: true, mono: true, sortField: 'key', sortValue: keyPath },
      },
      {
        id: 'namespace',
        header: t('translations.columns.namespace'),
        cell: ({ row }) => row.original.namespace,
        meta: { mono: true, defaultVisible: false, sortField: 'namespace', sortValue: (item) => item.namespace },
      },
      ...SUPPORTED_LANGUAGES.map(
        (language): GridColumnDef<UiTranslationKeyRead> => ({
          id: language,
          header: t(`translations.languages.${language}`),
          cell: ({ row }) => {
            const value = row.original[language]
            return value ? (
              <Group gap={6} wrap="nowrap">
                <Text span size="sm" truncate>
                  {value.value}
                </Text>
                {value.source === 'platform' && (
                  <Badge size="xs" variant="light" color="grape" style={{ flexShrink: 0 }}>
                    {t('translations.status.platform')}
                  </Badge>
                )}
              </Group>
            ) : (
              <Text span size="sm" c={slate[4]} fs="italic">
                {t('translations.status.absent')}
              </Text>
            )
          },
          meta: { sortField: language, sortValue: (item) => item[language]?.value },
        }),
      ),
      {
        id: 'updatedAt',
        header: t('translations.columns.updatedAt'),
        cell: ({ row }) => {
          const stamp = lastChanged(row.original)
          return stamp ? formatDateTime(stamp, locale) : '—'
        },
        meta: { align: 'right', defaultVisible: false, sortField: 'updatedAt', sortValue: lastChanged },
      },
    ],
    [t, locale],
  )
  const columnRegistry = useMemo(() => columnRegistryOf(columns), [columns])
  const columnLayout = useMemo(() => gridPrefs.columnLayout ?? defaultColumnLayout(columnRegistry), [gridPrefs.columnLayout, columnRegistry])
  const gridRows = useMemo(() => sortRowsInBrowser(items, sort, columns, locale), [items, sort, columns, locale])

  // A sort orders the whole result: while one is set, load what is left.
  const sorting = sort.length > 0
  const { hasNextPage, isFetchingNextPage, fetchNextPage } = list
  useEffect(() => {
    if (sorting && hasNextPage && !isFetchingNextPage) void fetchNextPage()
  }, [sorting, hasNextPage, isFetchingNextPage, fetchNextPage])

  // ADR-061: the one definition of what can be done to a key from the list.
  const rowMenu = (item: UiTranslationKeyRead): RowMenuGroups => ({
    edit: [
      canWrite
        ? { label: t('translations.open'), icon: <Pencil size={16} />, onClick: () => updateUrl({ key: keyPath(item) }) }
        : { label: t('translations.view'), icon: <Eye size={16} />, onClick: () => updateUrl({ key: keyPath(item) }) },
    ],
  })

  // A published or reverted row replaces its copy in every cached page.
  const replaceRow = (updated: UiTranslationRead) => {
    queryClient.setQueriesData<{ pages: UiTranslationKeyPage[]; pageParams: unknown[] }>({ queryKey: ['ui-translations'] }, (data) =>
      data && 'pages' in data
        ? {
            ...data,
            pages: data.pages.map((page) => ({
              ...page,
              items: page.items.map((item) =>
                item.namespace === updated.namespace && item.key === updated.key ? { ...item, [updated.language]: updated } : item,
              ),
            })),
          }
        : data,
    )
  }

  const afterWrite = async () => {
    await reloadPublishedTranslations()
    void queryClient.invalidateQueries({ queryKey: ['ui-translations'], refetchType: 'none' })
  }

  const publish = async (changes: { language: SupportedLanguage; value: string; version: number }[]): Promise<PublishOutcome[]> => {
    if (!selected) return []
    const outcomes: PublishOutcome[] = []
    for (const change of changes) {
      try {
        const updated = await api.put<UiTranslationRead>(
          rowPath({ ...selected, language: change.language }),
          { value: change.value },
          { 'If-Match': String(change.version) },
        )
        replaceRow(updated)
        outcomes.push({ language: change.language, error: null })
      } catch (err) {
        outcomes.push({ language: change.language, error: apiRefusalText(t, err, 'translations.loadError') })
      }
    }
    if (outcomes.some((o) => !o.error)) await afterWrite()
    return outcomes
  }

  const revert = async (language: SupportedLanguage, version: number): Promise<string | null> => {
    if (!selected) return null
    const path = `${rowPath({ ...selected, language })}/revert`
    try {
      const updated = await api.post<UiTranslationRead>(path, undefined, {
        'If-Match': String(version),
        ...revertKey.headers({ path, version }),
      })
      revertKey.renew()
      replaceRow(updated)
      await afterWrite()
      return null
    } catch (err) {
      return apiRefusalText(t, err, 'translations.loadError')
    }
  }

  const namespaceOptions = useMemo(
    () => [{ value: '', label: t('translations.namespace.all') }, ...(namespaces.data?.items ?? []).map((ns) => ({ value: ns, label: ns }))],
    [namespaces.data, t],
  )

  const nothingImported = firstPage && firstPage.total === 0 && !debouncedQuery && !namespace && !editedOnly

  return (
    <Stack gap="md">
      {selected && (
        <FourLanguageEditor
          title={keyPath(selected)}
          rows={rows}
          canWrite={canWrite}
          onPublish={publish}
          onRevert={revert}
          onClose={() => updateUrl({ key: null })}
        />
      )}

      <Card withBorder padding="lg" radius="md">
        <Stack gap="md">
          <Group gap="md" align="flex-end" wrap="wrap">
            <TextInput
              label={t('translations.search.label')}
              placeholder={t('translations.search.placeholder')}
              leftSection={<Search size={14} />}
              value={query}
              onChange={(event) => setQuery(event.currentTarget.value)}
              style={{ flex: '1 1 260px' }}
            />
            <Select
              label={t('translations.namespace.label')}
              data={namespaceOptions}
              value={namespace}
              onChange={(value) => updateUrl({ ns: value || null, key: null })}
              allowDeselect={false}
              searchable
              style={{ flex: '0 1 240px' }}
            />
            <Checkbox
              label={t('translations.editedOnly')}
              checked={editedOnly}
              onChange={(event) => updateUrl({ edited: event.currentTarget.checked ? '1' : null })}
              mb={8}
            />
            <Group ml="auto">
              <ColumnConfigPanel registry={columnRegistry} layout={columnLayout} onLayoutChange={gridPrefs.setColumnLayout} labels={columnPanelLabels} />
            </Group>
          </Group>

          {firstPage && (
            <Text size="sm" c={slate[5]}>
              {firstPage.totalIsEstimate
                ? t('translations.countEstimate', { count: firstPage.total })
                : t('translations.count', { count: firstPage.total })}
            </Text>
          )}

          {list.isError && (
            <Alert color="red" variant="light">
              {apiRefusalText(t, list.error, 'translations.loadError')}
            </Alert>
          )}
          {nothingImported && (
            <Alert color="yellow" variant="light">
              {t('translations.noSeed')}
            </Alert>
          )}
          {firstPage && items.length === 0 && !nothingImported && (
            <Text size="sm" c={slate[5]}>
              {t('translations.empty')}
            </Text>
          )}

          {items.length > 0 && (
            <DataGrid<UiTranslationKeyRead>
              ariaLabel={t('translations.gridLabel')}
              columns={columns}
              rows={gridRows}
              getRowId={keyPath}
              sort={sort}
              onSortChange={setSort}
              density={density}
              loading={false}
              refetching={sorting && hasNextPage}
              fetchingNextPage={isFetchingNextPage}
              hasNextPage={hasNextPage}
              onLoadMore={() => void fetchNextPage()}
              total={firstPage?.total ?? null}
              totalIsEstimate={firstPage?.totalIsEstimate ?? false}
              isFiltered={Boolean(debouncedQuery || namespace || editedOnly)}
              labels={labels}
              emptyState={{ icon: null, title: t('translations.empty'), description: '' }}
              rowActions={rowMenu}
              columnLayout={columnLayout}
              onColumnLayoutChange={gridPrefs.setColumnLayout}
            />
          )}
        </Stack>
      </Card>

      <MissingTranslationsCard />
    </Stack>
  )
}
