import { useMemo, useState } from 'react'
import { Alert, Button, Group, Paper, Skeleton, Stack, Table, Tabs, Text, Title } from '@mantine/core'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import { useTranslation } from 'react-i18next'
import { useSearchParams } from 'react-router-dom'
import { Badge, LanguageBadge, useSetBreadcrumb } from '@nexotec/ui-kit'
import { api } from '../api/client'
import type { StandardTextCategory, StandardTextList, StandardTextRegistryRead } from '../api/types'
import { useAuth } from '../auth/AuthContext'
import { StandardTextEditor } from '../components/standardTexts/StandardTextEditor'
import { TEXT_LANGUAGES, effective, entryKind, entryTitle, groupTexts, seedChanged, type TextEntry } from '../components/standardTexts/standardTextModel'
import { SUPPORTED_LANGUAGES, type SupportedLanguage } from '../i18n'
import { TEXT_CATEGORY_VALUES } from '../userOptions'

const standardTextsKey = (dealershipId: string) => ['standard-texts', dealershipId]

/**
 * Dealer Administration › Standard texts (KAN-208; PRD "Standard texts";
 * A-RULE-0: the one place a Betrieb's wording is configured). The library
 * by category — the seeded templates labelled «Vorlage — bitte prüfen»
 * (FR-A-08), the Betrieb's adaptations and its own texts — and one editor
 * with language tabs, the target's placeholders only and a preview on sample
 * data (FR-A-07). Any role reads; a category is written by the manager or by
 * a user holding its delegation (FR-A-28): the library says which
 * (`writableCategories`), and the editor offers no write beyond it — the
 * server's gate stays the authority. The category tab lives in the URL
 * (ADR-056).
 */
export function StandardTextsPage() {
  const { t, i18n } = useTranslation()
  useSetBreadcrumb([t('shell.nav.administration'), t('standardTexts.title')])
  const { activeDealership } = useAuth()
  const dealershipId = activeDealership?.id ?? ''
  const queryClient = useQueryClient()
  const [params, setParams] = useSearchParams()
  const category = (TEXT_CATEGORY_VALUES as string[]).includes(params.get('category') ?? '')
    ? (params.get('category') as StandardTextCategory)
    : 'document'
  const uiLanguage: SupportedLanguage = (SUPPORTED_LANGUAGES as readonly string[]).includes(i18n.language)
    ? (i18n.language as SupportedLanguage)
    : 'de'
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
  const targets = (registry.data?.targets ?? []).filter((target) => target.category === category)
  const inCategory = entries.filter((entry) => entry.category === category)
  const editingEntry = editing?.code ? (entries.find((entry) => entry.code === editing.code) ?? null) : null

  const countOf = (value: StandardTextCategory) => entries.filter((entry) => entry.category === value).length

  return (
    <Stack gap="md">
      <Group justify="space-between" align="flex-start">
        <div>
          <Title order={2}>{t('standardTexts.title')}</Title>
          <Text c="dimmed" fz="sm">
            {t('standardTexts.subtitle')}
          </Text>
        </div>
        {writable.has(category) && targets.length > 0 && (
          <Button onClick={() => setEditing({ code: null })}>{t('standardTexts.newText')}</Button>
        )}
      </Group>
      <Alert color="yellow" variant="light">
        {t('standardTexts.disclaimer')}
      </Alert>

      <Tabs value={category} onChange={(value) => value && setParams({ category: value }, { replace: true })}>
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
      {library.data && !writable.has(category) && (
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
        <Paper withBorder radius="md">
          <Table striped highlightOnHover aria-label={t(`standardTexts.category.${category}`)}>
            <Table.Thead>
              <Table.Tr>
                <Table.Th>{t('standardTexts.columns.title')}</Table.Th>
                <Table.Th>{t('standardTexts.columns.appliesTo')}</Table.Th>
                <Table.Th>{t('standardTexts.columns.slot')}</Table.Th>
                <Table.Th>{t('standardTexts.columns.source')}</Table.Th>
                <Table.Th>{t('standardTexts.columns.languages')}</Table.Th>
                <Table.Th>{t('standardTexts.columns.default')}</Table.Th>
                <Table.Th />
              </Table.Tr>
            </Table.Thead>
            <Table.Tbody>
              {inCategory.map((entry) => (
                <EntryRow key={entry.code} entry={entry} uiLanguage={uiLanguage} writable={writable.has(category)} onOpen={() => setEditing({ code: entry.code })} />
              ))}
            </Table.Tbody>
          </Table>
        </Paper>
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
          writable={writable.has(category)}
          uiLanguage={uiLanguage}
          onSaved={() => void queryClient.invalidateQueries({ queryKey: standardTextsKey(dealershipId) })}
        />
      )}
    </Stack>
  )
}

function EntryRow({ entry, uiLanguage, writable, onOpen }: { entry: TextEntry; uiLanguage: SupportedLanguage; writable: boolean; onOpen: () => void }) {
  const { t } = useTranslation()
  const kind = entryKind(entry)
  const shown = effective(entry, uiLanguage)
  const languages = TEXT_LANGUAGES.filter((language) => effective(entry, language) !== null)
  return (
    <Table.Tr>
      <Table.Td>
        <Text fw={500} fz="sm">
          {entryTitle(entry, uiLanguage)}
        </Text>
        {shown && shown.active === false && (
          <Text fz="xs" c="dimmed">
            {t('standardTexts.inactive')}
          </Text>
        )}
      </Table.Td>
      <Table.Td>{t(`standardTexts.appliesTo.${entry.appliesTo}`)}</Table.Td>
      <Table.Td>{t(`standardTexts.slot.${entry.slot}`)}</Table.Td>
      <Table.Td>
        <Group gap={6}>
          <Badge tone={kind === 'template' ? 'warning' : kind === 'adapted' ? 'purple' : 'slate'}>{t(`standardTexts.badge.${kind}`)}</Badge>
          {seedChanged(entry) && (
            <Badge tone="destructive" dot>
              {t('standardTexts.badge.seedChanged')}
            </Badge>
          )}
        </Group>
      </Table.Td>
      <Table.Td>
        <Group gap={6}>
          {languages.map((language) => (
            <LanguageBadge key={language} language={language} />
          ))}
        </Group>
      </Table.Td>
      <Table.Td>{shown?.isDefault ? t('common.yes') : '—'}</Table.Td>
      <Table.Td>
        <Button size="xs" variant="light" onClick={onOpen}>
          {writable ? t('standardTexts.edit') : t('standardTexts.view')}
        </Button>
      </Table.Td>
    </Table.Tr>
  )
}
