import { useEffect, useMemo, useState } from 'react'
import { useSearchParams } from 'react-router-dom'
import { Alert, Badge, Card, Group, Select, Stack, Text, TextInput } from '@mantine/core'
import { useDebouncedValue } from '@mantine/hooks'
import { useQuery, useQueryClient } from '@tanstack/react-query'
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
import type { StandardTextCategory, StandardTextSeedGroup, StandardTextSeedList, StandardTextSeedRead } from '../../api/types'
import { useCanWrite } from '../../auth/useCanWrite'
import { SUPPORTED_LANGUAGES, toSwissLocale, type SupportedLanguage } from '../../i18n'
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

const BASE = '/platform/standard-text-seeds'
const CATEGORIES: StandardTextCategory[] = ['document', 'email', 'sms', 'whatsapp', 'internal_note', 'workshop']
const GRID_KEY = 'platform.translations.standardTextSeeds'

type Field = 'title' | 'body'

/** The latest change to any language of the seed. */
function lastChanged(group: StandardTextSeedGroup): string | null {
  const stamps = SUPPORTED_LANGUAGES.map((language) => group[language]?.updatedAt).filter((stamp): stamp is string => Boolean(stamp))
  return stamps.length > 0 ? stamps.reduce((latest, stamp) => (stamp > latest ? stamp : latest)) : null
}

function editorRows(group: StandardTextSeedGroup, field: Field): Partial<Record<SupportedLanguage, EditorLanguageRow>> {
  const rows: Partial<Record<SupportedLanguage, EditorLanguageRow>> = {}
  for (const language of SUPPORTED_LANGUAGES) {
    const row = group[language]
    if (!row) continue
    const value = field === 'title' ? row.title : row.body
    const shipped = field === 'title' ? row.shippedTitle : row.shippedBody
    // A seed the release does not ship has nothing to diff against: its own text.
    const seedValue = shipped ?? value
    rows[language] = { value, seedValue, edited: value !== seedValue, version: row.version }
  }
  return rows
}

/**
 * KAN-209 on KAN-208's library — the standard-text seeds source of the
 * Translations panel (D-A-23, FR-A-29): the texts Nexotec ships to every
 * Betrieb, one row per code, title and body each in the four-column editor
 * with the diff against the shipped wording, publish and revert. A seed edit
 * never touches a Betrieb's adaptation; the Betrieb's row is flagged "the
 * standard text has changed". Platform staff write at the API
 * (`ui_translations`, the matrix row "UI translations and seed library");
 * anyone else reads. Search, category, the sort and the open code are in
 * the URL.
 *
 * KAN-516 (Anto, 2026-11-13, option C): the library is the kit's grid — the
 * row menu opens a seed (ADR-061), the Columns panel is the user's (ADR-060)
 * — and, a short fixed list the endpoint does not sort, it sorts in the
 * browser. No sort keeps the endpoint's order.
 */
export function StandardTextSeedsPanel() {
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
  const category = searchParams.get('category') ?? ''
  const openCode = searchParams.get('code')
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

  const listKey = ['standard-text-seeds', debouncedQuery, category] as const
  const list = useQuery({
    queryKey: listKey,
    queryFn: () => {
      const params = new URLSearchParams()
      if (debouncedQuery) params.set('q', debouncedQuery)
      if (category) params.set('category', category)
      const qs = params.toString()
      return api.get<StandardTextSeedList>(qs ? `${BASE}?${qs}` : BASE)
    },
  })
  const items = useMemo(() => list.data?.items ?? [], [list.data])
  const selected = useMemo(() => items.find((group) => group.code === openCode) ?? null, [items, openCode])
  const titleRows = useMemo(() => (selected ? editorRows(selected, 'title') : {}), [selected])
  const bodyRows = useMemo(() => (selected ? editorRows(selected, 'body') : {}), [selected])

  const columns: GridColumnDef<StandardTextSeedGroup>[] = useMemo(() => {
    const target = (group: StandardTextSeedGroup) =>
      `${t(`standardTexts.category.${group.category}`)} · ${t(`standardTexts.appliesTo.${group.appliesTo}`)} · ${t(`standardTexts.slot.${group.slot}`)}`
    return [
      {
        id: 'code',
        header: t('translations.seeds.columns.code'),
        cell: ({ row }) => row.original.code,
        meta: { locked: true, mono: true, sortField: 'code', sortValue: (group) => group.code },
      },
      {
        id: 'target',
        header: t('translations.seeds.columns.target'),
        cell: ({ row }) => target(row.original),
        meta: { sortField: 'target', sortValue: target },
      },
      {
        id: 'category',
        header: t('translations.seeds.columns.category'),
        cell: ({ row }) => t(`standardTexts.category.${row.original.category}`),
        meta: { defaultVisible: false, sortField: 'category', sortValue: (group) => t(`standardTexts.category.${group.category}`) },
      },
      {
        id: 'appliesTo',
        header: t('translations.seeds.columns.appliesTo'),
        cell: ({ row }) => t(`standardTexts.appliesTo.${row.original.appliesTo}`),
        meta: { defaultVisible: false, sortField: 'appliesTo', sortValue: (group) => t(`standardTexts.appliesTo.${group.appliesTo}`) },
      },
      {
        id: 'slot',
        header: t('translations.seeds.columns.slot'),
        cell: ({ row }) => t(`standardTexts.slot.${row.original.slot}`),
        meta: { defaultVisible: false, sortField: 'slot', sortValue: (group) => t(`standardTexts.slot.${group.slot}`) },
      },
      ...SUPPORTED_LANGUAGES.map(
        (language): GridColumnDef<StandardTextSeedGroup> => ({
          id: language,
          header: t(`translations.languages.${language}`),
          cell: ({ row }) => {
            const seed = row.original[language]
            return seed ? (
              <Group gap={6} wrap="nowrap">
                <Text span size="sm" truncate>
                  {seed.title}
                </Text>
                {seed.edited && (
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
          meta: { sortField: language, sortValue: (group) => group[language]?.title },
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
    ]
  }, [t, locale])
  const columnRegistry = useMemo(() => columnRegistryOf(columns), [columns])
  const columnLayout = useMemo(() => gridPrefs.columnLayout ?? defaultColumnLayout(columnRegistry), [gridPrefs.columnLayout, columnRegistry])
  const gridRows = useMemo(() => sortRowsInBrowser(items, sort, columns, locale), [items, sort, columns, locale])

  // ADR-061: the one definition of what can be done to a seed from the list.
  const rowMenu = (group: StandardTextSeedGroup): RowMenuGroups => ({
    edit: [
      canWrite
        ? { label: t('translations.open'), icon: <Pencil size={16} />, onClick: () => updateUrl({ code: group.code }) }
        : { label: t('translations.view'), icon: <Eye size={16} />, onClick: () => updateUrl({ code: group.code }) },
    ],
  })

  // A written seed row replaces its copy in every cached listing.
  const replaceRow = (code: string, updated: StandardTextSeedRead) => {
    queryClient.setQueriesData<StandardTextSeedList>({ queryKey: ['standard-text-seeds'] }, (data) =>
      data
        ? { ...data, items: data.items.map((group) => (group.code === code ? { ...group, [updated.language]: updated } : group)) }
        : data,
    )
  }

  const publish =
    (field: Field) =>
    async (changes: { language: SupportedLanguage; value: string; version: number }[]): Promise<PublishOutcome[]> => {
      if (!selected) return []
      const outcomes: PublishOutcome[] = []
      for (const change of changes) {
        const row = selected[change.language]
        if (!row) continue
        try {
          const updated = await api.put<StandardTextSeedRead>(
            `${BASE}/${row.id}`,
            { [field]: change.value },
            { 'If-Match': String(change.version) },
          )
          replaceRow(selected.code, updated)
          outcomes.push({ language: change.language, error: null })
        } catch (err) {
          outcomes.push({ language: change.language, error: apiRefusalText(t, err, 'translations.seeds.loadError') })
        }
      }
      return outcomes
    }

  const revert = async (language: SupportedLanguage, version: number): Promise<string | null> => {
    const row = selected?.[language]
    if (!selected || !row) return null
    const path = `${BASE}/${row.id}/revert`
    try {
      const updated = await api.post<StandardTextSeedRead>(path, undefined, {
        'If-Match': String(version),
        ...revertKey.headers({ path, version }),
      })
      revertKey.renew()
      replaceRow(selected.code, updated)
      return null
    } catch (err) {
      return apiRefusalText(t, err, 'translations.seeds.loadError')
    }
  }

  const categoryOptions = useMemo(
    () => [
      { value: '', label: t('translations.seeds.category.all') },
      ...CATEGORIES.map((c) => ({ value: c, label: t(`standardTexts.category.${c}`) })),
    ],
    [t],
  )

  return (
    <Stack gap="md">
      <Text size="sm" c={slate[5]}>
        {t('translations.seeds.subtitle')}
      </Text>
      <Alert color="yellow" variant="light">
        {t('standardTexts.disclaimer')}
      </Alert>

      {selected && (
        <Stack gap="md">
          <FourLanguageEditor
            title={selected.code}
            headingLabel={t('translations.seeds.fieldTitle')}
            placeholderExample="{{document.date}}"
            rows={titleRows}
            canWrite={canWrite}
            onPublish={publish('title')}
            onRevert={revert}
            onClose={() => updateUrl({ code: null })}
          />
          <FourLanguageEditor
            title={selected.code}
            headingLabel={t('translations.seeds.fieldBody')}
            placeholderExample="{{document.date}}"
            rows={bodyRows}
            canWrite={canWrite}
            onPublish={publish('body')}
            onRevert={revert}
          />
        </Stack>
      )}

      <Card withBorder padding="lg" radius="md">
        <Stack gap="md">
          <Group gap="md" align="flex-end" wrap="wrap">
            <TextInput
              label={t('translations.seeds.search.label')}
              placeholder={t('translations.seeds.search.placeholder')}
              leftSection={<Search size={14} />}
              value={query}
              onChange={(event) => setQuery(event.currentTarget.value)}
              style={{ flex: '1 1 260px' }}
            />
            <Select
              label={t('translations.seeds.category.label')}
              data={categoryOptions}
              value={category}
              onChange={(value) => updateUrl({ category: value || null, code: null })}
              allowDeselect={false}
              style={{ flex: '0 1 240px' }}
            />
            <Group ml="auto">
              <ColumnConfigPanel registry={columnRegistry} layout={columnLayout} onLayoutChange={gridPrefs.setColumnLayout} labels={columnPanelLabels} />
            </Group>
          </Group>

          {list.data && (
            <Text size="sm" c={slate[5]}>
              {t('translations.seeds.count', { count: items.length })}
            </Text>
          )}
          {list.isError && (
            <Alert color="red" variant="light">
              {apiRefusalText(t, list.error, 'translations.seeds.loadError')}
            </Alert>
          )}
          {list.data && items.length === 0 && (
            <Text size="sm" c={slate[5]}>
              {t('translations.seeds.empty')}
            </Text>
          )}

          {items.length > 0 && (
            <DataGrid<StandardTextSeedGroup>
              ariaLabel={t('translations.seeds.gridLabel')}
              columns={columns}
              rows={gridRows}
              getRowId={(group) => group.code}
              sort={sort}
              onSortChange={setSort}
              density={density}
              loading={false}
              fetchingNextPage={false}
              hasNextPage={false}
              onLoadMore={() => {}}
              total={gridRows.length}
              totalIsEstimate={false}
              isFiltered={Boolean(debouncedQuery || category)}
              labels={labels}
              emptyState={{ icon: null, title: t('translations.seeds.empty'), description: '' }}
              rowActions={rowMenu}
              columnLayout={columnLayout}
              onColumnLayoutChange={gridPrefs.setColumnLayout}
            />
          )}
        </Stack>
      </Card>
    </Stack>
  )
}
