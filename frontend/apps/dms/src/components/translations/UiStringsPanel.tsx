import { useEffect, useMemo, useState } from 'react'
import { useSearchParams } from 'react-router-dom'
import { Alert, Badge, Button, Card, Checkbox, Group, Select, Stack, Table, Text, TextInput } from '@mantine/core'
import { useDebouncedValue } from '@mantine/hooks'
import { useInfiniteQuery, useQuery, useQueryClient } from '@tanstack/react-query'
import { Search } from 'lucide-react'
import { useTranslation } from 'react-i18next'
import { slate } from '@nexotec/ui-kit'
import { api } from '../../api/client'
import type { UiTranslationKeyPage, UiTranslationKeyRead, UiTranslationNamespaces, UiTranslationRead } from '../../api/types'
import { useCanWrite } from '../../auth/useCanWrite'
import { SUPPORTED_LANGUAGES, type SupportedLanguage } from '../../i18n'
import { reloadPublishedTranslations } from '../../i18n/runtimeTranslations'
import { useIdempotencyKey } from '../../hooks/useIdempotencyKey'
import { apiRefusalText } from '../../utils/apiRefusal'
import { FourLanguageEditor, type EditorLanguageRow, type PublishOutcome } from './FourLanguageEditor'
import { MissingTranslationsCard } from './MissingTranslationsCard'

const PAGE_SIZE = 50
const BASE = '/platform/ui-translations'

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
 * are hidden, never the data). Search, area, «edited only» and the open key
 * live in the URL (ADR-056).
 */
export function UiStringsPanel() {
  const { t } = useTranslation()
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
            <Table.ScrollContainer minWidth={900}>
              <Table striped highlightOnHover verticalSpacing="xs" fz="sm">
                <Table.Thead>
                  <Table.Tr>
                    <Table.Th>{t('translations.columns.key')}</Table.Th>
                    {SUPPORTED_LANGUAGES.map((language) => (
                      <Table.Th key={language}>{t(`translations.languages.${language}`)}</Table.Th>
                    ))}
                    <Table.Th>{t('translations.columns.actions')}</Table.Th>
                  </Table.Tr>
                </Table.Thead>
                <Table.Tbody>
                  {items.map((item) => {
                    const path = keyPath(item)
                    return (
                      <Table.Tr key={path} aria-selected={path === openKey}>
                        <Table.Td style={{ fontFamily: 'monospace', wordBreak: 'break-all', maxWidth: 260 }}>{path}</Table.Td>
                        {SUPPORTED_LANGUAGES.map((language) => {
                          const row = item[language]
                          return (
                            <Table.Td key={language} style={{ maxWidth: 240 }}>
                              {row ? (
                                <Group gap={6} wrap="nowrap" align="flex-start">
                                  <Text size="sm" lineClamp={2} style={{ wordBreak: 'break-word' }}>
                                    {row.value}
                                  </Text>
                                  {row.source === 'platform' && (
                                    <Badge size="xs" variant="light" color="grape" style={{ flexShrink: 0 }}>
                                      {t('translations.status.platform')}
                                    </Badge>
                                  )}
                                </Group>
                              ) : (
                                <Text size="sm" c={slate[4]} fs="italic">
                                  {t('translations.status.absent')}
                                </Text>
                              )}
                            </Table.Td>
                          )
                        })}
                        <Table.Td>
                          <Button size="compact-sm" variant="light" onClick={() => updateUrl({ key: path })}>
                            {canWrite ? t('translations.open') : t('translations.view')}
                          </Button>
                        </Table.Td>
                      </Table.Tr>
                    )
                  })}
                </Table.Tbody>
              </Table>
            </Table.ScrollContainer>
          )}

          {list.hasNextPage && (
            <Group justify="center">
              <Button variant="default" onClick={() => void list.fetchNextPage()} loading={list.isFetchingNextPage}>
                {t('translations.loadMore')}
              </Button>
            </Group>
          )}
        </Stack>
      </Card>

      <MissingTranslationsCard />
    </Stack>
  )
}
