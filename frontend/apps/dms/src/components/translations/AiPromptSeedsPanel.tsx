import { useMemo } from 'react'
import { Alert, Badge, Card, Group, Stack, Text } from '@mantine/core'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import { useTranslation } from 'react-i18next'
import { slate } from '@nexotec/ui-kit'
import { api } from '../../api/client'
import type { AiPromptSeedGroup, AiPromptSeedList, AiPromptSeedRead } from '../../api/types'
import { useCanWrite } from '../../auth/useCanWrite'
import { SUPPORTED_LANGUAGES, type SupportedLanguage } from '../../i18n'
import { useIdempotencyKey } from '../../hooks/useIdempotencyKey'
import { apiRefusalText } from '../../utils/apiRefusal'
import { FourLanguageEditor, type EditorLanguageRow, type PublishOutcome } from './FourLanguageEditor'

const BASE = '/platform/ai-prompt-seeds'
const LIST_KEY = ['ai-prompt-seeds'] as const

type Field = 'title' | 'systemPrompt' | 'userPrompt'
const SHIPPED: Record<Field, 'shippedTitle' | 'shippedSystemPrompt' | 'shippedUserPrompt'> = {
  title: 'shippedTitle',
  systemPrompt: 'shippedSystemPrompt',
  userPrompt: 'shippedUserPrompt',
}

function editorRows(group: AiPromptSeedGroup, field: Field): Partial<Record<SupportedLanguage, EditorLanguageRow>> {
  const rows: Partial<Record<SupportedLanguage, EditorLanguageRow>> = {}
  for (const language of SUPPORTED_LANGUAGES) {
    const row = group[language]
    if (!row) continue
    const value = row[field]
    // A seed the release does not ship has nothing to diff against: its own text.
    const seedValue = row[SHIPPED[field]] ?? value
    rows[language] = { value, seedValue, edited: value !== seedValue, version: row.version }
  }
  return rows
}

/**
 * KAN-227 (D-A-33, D-A-23) — the AI prompt seeds source of the Translations
 * panel: the prompts Nexotec ships to every Betrieb (`vehicle_listing` in v1),
 * per code its title, instructions and request, each in the four-column
 * editor with the diff against the shipped wording, publish and revert. A seed
 * edit never touches a Betrieb's adaptation; the Betrieb's row is flagged
 * «the default prompt has changed». Platform staff write at the API
 * (`ui_translations`); anyone else reads. The codes are a closed registry, so
 * the panel lists them all, each with the placeholders its prompts may use.
 */
export function AiPromptSeedsPanel() {
  const { t } = useTranslation()
  const canWrite = useCanWrite('ui_translations')
  const queryClient = useQueryClient()
  const revertKey = useIdempotencyKey()
  const list = useQuery({ queryKey: LIST_KEY, queryFn: () => api.get<AiPromptSeedList>(BASE) })
  const items = useMemo(() => list.data?.items ?? [], [list.data])

  const replaceRow = (code: string, updated: AiPromptSeedRead) => {
    queryClient.setQueryData<AiPromptSeedList>(LIST_KEY, (data) =>
      data ? { ...data, items: data.items.map((g) => (g.code === code ? { ...g, [updated.language]: updated } : g)) } : data,
    )
  }

  const publish =
    (group: AiPromptSeedGroup, field: Field) =>
    async (changes: { language: SupportedLanguage; value: string; version: number }[]): Promise<PublishOutcome[]> => {
      const outcomes: PublishOutcome[] = []
      for (const change of changes) {
        const row = group[change.language]
        if (!row) continue
        try {
          const updated = await api.put<AiPromptSeedRead>(`${BASE}/${row.id}`, { [field]: change.value }, { 'If-Match': String(change.version) })
          replaceRow(group.code, updated)
          outcomes.push({ language: change.language, error: null })
        } catch (err) {
          outcomes.push({ language: change.language, error: apiRefusalText(t, err, 'translations.aiPrompts.saveError') })
        }
      }
      return outcomes
    }

  const revert = (group: AiPromptSeedGroup) => async (language: SupportedLanguage, version: number): Promise<string | null> => {
    const row = group[language]
    if (!row) return null
    const path = `${BASE}/${row.id}/revert`
    try {
      const updated = await api.post<AiPromptSeedRead>(path, undefined, {
        'If-Match': String(version),
        ...revertKey.headers({ path, version }),
      })
      revertKey.renew()
      replaceRow(group.code, updated)
      return null
    } catch (err) {
      return apiRefusalText(t, err, 'translations.aiPrompts.saveError')
    }
  }

  return (
    <Stack gap="md">
      <Text size="sm" c={slate[5]}>
        {t('translations.aiPrompts.subtitle')}
      </Text>
      {list.isError && (
        <Alert color="red" variant="light">
          {apiRefusalText(t, list.error, 'translations.aiPrompts.loadError')}
        </Alert>
      )}
      {list.data && items.length === 0 && (
        <Text size="sm" c={slate[5]}>
          {t('translations.aiPrompts.empty')}
        </Text>
      )}
      {items.map((group) => (
        <Card key={group.code} withBorder padding="lg" radius="md">
          <Stack gap="md">
            <Group gap="xs">
              <Text fw={600}>{t(`aiPrompts.codes.${group.code}`, group.code)}</Text>
              <Badge variant="light" color="gray" ff="monospace">
                {group.code}
              </Badge>
            </Group>
            <Text size="xs" c={slate[5]}>
              {t('aiPrompts.placeholders', { list: group.placeholders.map((p) => `{{ ${p} }}`).join(', ') })}
            </Text>
            <FourLanguageEditor
              title={group.code}
              headingLabel={t('aiPrompts.fields.title')}
              rows={editorRows(group, 'title')}
              canWrite={canWrite}
              onPublish={publish(group, 'title')}
              onRevert={revert(group)}
            />
            <FourLanguageEditor
              title={group.code}
              headingLabel={t('aiPrompts.fields.systemPrompt')}
              rows={editorRows(group, 'systemPrompt')}
              canWrite={canWrite}
              onPublish={publish(group, 'systemPrompt')}
              onRevert={revert(group)}
            />
            <FourLanguageEditor
              title={group.code}
              headingLabel={t('aiPrompts.fields.userPrompt')}
              placeholderExample={group.placeholders[0] ? `{{ ${group.placeholders[0]} }}` : undefined}
              rows={editorRows(group, 'userPrompt')}
              canWrite={canWrite}
              onPublish={publish(group, 'userPrompt')}
              onRevert={revert(group)}
            />
          </Stack>
        </Card>
      ))}
    </Stack>
  )
}
