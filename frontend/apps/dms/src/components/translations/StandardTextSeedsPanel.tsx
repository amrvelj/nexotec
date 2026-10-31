import { useEffect, useMemo, useState } from 'react'
import { useSearchParams } from 'react-router-dom'
import { Alert, Badge, Button, Card, Group, Select, Stack, Table, Text, TextInput } from '@mantine/core'
import { useDebouncedValue } from '@mantine/hooks'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import { Search } from 'lucide-react'
import { useTranslation } from 'react-i18next'
import { slate } from '@nexotec/ui-kit'
import { api } from '../../api/client'
import type { StandardTextCategory, StandardTextSeedGroup, StandardTextSeedList, StandardTextSeedRead } from '../../api/types'
import { useCanWrite } from '../../auth/useCanWrite'
import { SUPPORTED_LANGUAGES, type SupportedLanguage } from '../../i18n'
import { useIdempotencyKey } from '../../hooks/useIdempotencyKey'
import { apiRefusalText } from '../../utils/apiRefusal'
import { FourLanguageEditor, type EditorLanguageRow, type PublishOutcome } from './FourLanguageEditor'

const BASE = '/platform/standard-text-seeds'
const CATEGORIES: StandardTextCategory[] = ['document', 'email', 'sms', 'whatsapp', 'internal_note', 'workshop']

type Field = 'title' | 'body'

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
 * anyone else reads. Search, category and the open code are in the URL.
 */
export function StandardTextSeedsPanel() {
  const { t } = useTranslation()
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
            <Table.ScrollContainer minWidth={900}>
              <Table striped highlightOnHover verticalSpacing="xs" fz="sm">
                <Table.Thead>
                  <Table.Tr>
                    <Table.Th>{t('translations.seeds.columns.code')}</Table.Th>
                    <Table.Th>{t('translations.seeds.columns.target')}</Table.Th>
                    {SUPPORTED_LANGUAGES.map((language) => (
                      <Table.Th key={language}>{t(`translations.languages.${language}`)}</Table.Th>
                    ))}
                    <Table.Th>{t('translations.columns.actions')}</Table.Th>
                  </Table.Tr>
                </Table.Thead>
                <Table.Tbody>
                  {items.map((group) => (
                    <Table.Tr key={group.code} aria-selected={group.code === openCode}>
                      <Table.Td style={{ fontFamily: 'monospace', wordBreak: 'break-all', maxWidth: 220 }}>{group.code}</Table.Td>
                      <Table.Td>
                        <Text size="sm">
                          {t(`standardTexts.category.${group.category}`)} · {t(`standardTexts.appliesTo.${group.appliesTo}`)} ·{' '}
                          {t(`standardTexts.slot.${group.slot}`)}
                        </Text>
                      </Table.Td>
                      {SUPPORTED_LANGUAGES.map((language) => {
                        const row = group[language]
                        return (
                          <Table.Td key={language} style={{ maxWidth: 220 }}>
                            {row ? (
                              <Group gap={6} wrap="nowrap" align="flex-start">
                                <Text size="sm" lineClamp={2} style={{ wordBreak: 'break-word' }}>
                                  {row.title}
                                </Text>
                                {row.edited && (
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
                        <Button size="compact-sm" variant="light" onClick={() => updateUrl({ code: group.code })}>
                          {canWrite ? t('translations.open') : t('translations.view')}
                        </Button>
                      </Table.Td>
                    </Table.Tr>
                  ))}
                </Table.Tbody>
              </Table>
            </Table.ScrollContainer>
          )}
        </Stack>
      </Card>
    </Stack>
  )
}
