import { useEffect, useMemo, useState } from 'react'
import { Alert, Anchor, Badge, Button, Card, Group, Skeleton, Stack, Tabs, Text, Textarea, Title } from '@mantine/core'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import { useTranslation } from 'react-i18next'
import { Link } from 'react-router-dom'
import { ConnectionStatusBadge, KeyValueRow, slate, useSetBreadcrumb } from '@nexotec/ui-kit'
import { api } from '../api/client'
import type { AiPromptList, AiPromptRead } from '../api/types'
import { useAuth } from '../auth/AuthContext'
import { useCanWrite } from '../auth/useCanWrite'
import { useFieldHint } from '../hooks/useFieldHint'
import { useIdempotencyKey } from '../hooks/useIdempotencyKey'
import { SUPPORTED_LANGUAGES, type SupportedLanguage } from '../i18n'
import { translatedConnectionStatusLabel } from '../integrationOptions'
import { apiRefusalText } from '../utils/apiRefusal'

const promptsKey = (dealershipId: string) => ['ai-prompts', dealershipId]

/**
 * KAN-227 — Dealer Administration › KI-Texte (`/administration/ai`; D-A-33,
 * FR-A-36/38, A-RULE-0: the one place the Betrieb configures its AI). Two
 * things: which of the Betrieb's own AI connections writes the suggestions
 * (connected in Integrationen — bring your own key), and the Betrieb's
 * wording of each prompt per language — Nexotec's default until the manager
 * adapts it, «the default prompt has changed, review yours» when Nexotec's
 * moves on, and back to the default by removing the adaptation. The same
 * seed-and-override pattern as the standard texts. Inventory and sales read
 * (they use the prompt), the manager writes; never role-hidden (A-RULE-7).
 */
export function AiAdministrationPage() {
  const { t } = useTranslation()
  useSetBreadcrumb([t('shell.nav.administration'), t('aiPrompts.title')])
  const { activeDealership } = useAuth()
  const dealershipId = activeDealership?.id ?? ''
  const canWrite = useCanWrite('ai_prompts')
  const library = useQuery({
    queryKey: promptsKey(dealershipId),
    queryFn: () => api.get<AiPromptList>(`/dealerships/${dealershipId}/ai-prompts`),
    enabled: dealershipId !== '',
  })

  return (
    <Stack gap="lg">
      <Stack gap={2}>
        <Title order={2}>{t('aiPrompts.title')}</Title>
        <Text c={slate[5]} size="sm">
          {t('aiPrompts.subtitle')}
        </Text>
      </Stack>
      {library.isError && (
        <Alert color="red" variant="light">
          {apiRefusalText(t, library.error, 'aiPrompts.loadError')}
        </Alert>
      )}
      {library.isLoading && <Skeleton height={160} />}
      {library.data && (
        <>
          <ConnectionCard data={library.data} />
          {library.data.codes.map((entry) => (
            <PromptCard
              key={entry.code}
              dealershipId={dealershipId}
              code={entry.code}
              placeholders={entry.placeholders}
              rows={library.data.items.filter((row) => row.code === entry.code)}
              canWrite={canWrite}
            />
          ))}
        </>
      )}
    </Stack>
  )
}

function ConnectionCard({ data }: { data: AiPromptList }) {
  const { t } = useTranslation()
  const connection = data.connection
  return (
    <Card withBorder padding="lg" radius="md">
      <Stack gap="xs">
        <Text fw={600}>{t('aiPrompts.connection.title')}</Text>
        {connection ? (
          <>
            <KeyValueRow label={t('aiPrompts.connection.provider')}>
              <Group gap="xs">
                <Text size="sm">{connection.providerName}</Text>
                <ConnectionStatusBadge status={connection.status} label={translatedConnectionStatusLabel(t, connection.status)} />
              </Group>
            </KeyValueRow>
            <KeyValueRow label={t('aiPrompts.connection.name')}>{connection.displayName}</KeyValueRow>
            <Text size="xs" c={slate[5]}>
              {t('aiPrompts.connection.hint')}
            </Text>
          </>
        ) : (
          <Alert color="yellow" variant="light">
            {t('aiPrompts.connection.none')}
          </Alert>
        )}
        <Anchor component={Link} to="/administration/integrations" fz="sm">
          {t('aiPrompts.connection.manage')}
        </Anchor>
      </Stack>
    </Card>
  )
}

function PromptCard({
  dealershipId,
  code,
  placeholders,
  rows,
  canWrite,
}: {
  dealershipId: string
  code: string
  placeholders: string[]
  rows: AiPromptRead[]
  canWrite: boolean
}) {
  const { t } = useTranslation()
  const [language, setLanguage] = useState<SupportedLanguage>('de')
  const languages = SUPPORTED_LANGUAGES.filter((l) => rows.some((row) => row.language === l && row.source === 'platform_seed'))
  return (
    <Card withBorder padding="lg" radius="md">
      <Stack gap="md">
        <Group gap="xs">
          <Text fw={600}>{t(`aiPrompts.codes.${code}`)}</Text>
        </Group>
        <Text size="xs" c={slate[5]}>
          {t('aiPrompts.placeholders', { list: placeholders.map((p) => `{{ ${p} }}`).join(', ') })}
        </Text>
        <Tabs value={language} onChange={(value) => value && setLanguage(value as SupportedLanguage)}>
          <Tabs.List aria-label={t('aiPrompts.languagesLabel')}>
            {languages.map((l) => (
              <Tabs.Tab key={l} value={l}>
                {t(`translations.languages.${l}`)}
              </Tabs.Tab>
            ))}
          </Tabs.List>
        </Tabs>
        <PromptEditor
          key={`${code}-${language}`}
          dealershipId={dealershipId}
          code={code}
          language={language}
          seed={rows.find((row) => row.language === language && row.source === 'platform_seed')}
          own={rows.find((row) => row.language === language && row.source === 'dealer')}
          canWrite={canWrite}
        />
      </Stack>
    </Card>
  )
}

function PromptEditor({
  dealershipId,
  code,
  language,
  seed,
  own,
  canWrite,
}: {
  dealershipId: string
  code: string
  language: SupportedLanguage
  seed: AiPromptRead | undefined
  own: AiPromptRead | undefined
  canWrite: boolean
}) {
  const { t } = useTranslation()
  const hint = useFieldHint()
  const queryClient = useQueryClient()
  const createKey = useIdempotencyKey()
  const effective = own ?? seed
  const [systemPrompt, setSystemPrompt] = useState(effective?.systemPrompt ?? '')
  const [userPrompt, setUserPrompt] = useState(effective?.userPrompt ?? '')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  useEffect(() => {
    setSystemPrompt(effective?.systemPrompt ?? '')
    setUserPrompt(effective?.userPrompt ?? '')
  }, [effective?.id, effective?.version, effective?.systemPrompt, effective?.userPrompt])
  const dirty = useMemo(
    () => effective !== undefined && (systemPrompt !== effective.systemPrompt || userPrompt !== effective.userPrompt),
    [effective, systemPrompt, userPrompt],
  )
  if (!seed || !effective) return null

  const base = `/dealerships/${dealershipId}/ai-prompts`
  const run = async (write: () => Promise<unknown>) => {
    setBusy(true)
    setError(null)
    try {
      await write()
      await queryClient.invalidateQueries({ queryKey: promptsKey(dealershipId) })
    } catch (err) {
      setError(apiRefusalText(t, err, 'aiPrompts.saveError'))
    } finally {
      setBusy(false)
    }
  }
  const save = () =>
    run(async () => {
      if (own) {
        await api.patch(`${base}/${own.id}`, { systemPrompt, userPrompt }, { 'If-Match': String(own.version) })
      } else {
        const body = { code, language, systemPrompt, userPrompt }
        await api.post(base, body, createKey.headers(body))
        createKey.renew()
      }
    })
  const markReviewed = () =>
    own && run(() => api.patch(`${base}/${own.id}`, { seedReviewed: true }, { 'If-Match': String(own.version) }))
  const backToDefault = () => own && run(() => api.delete(`${base}/${own.id}`, { 'If-Match': String(own.version) }))

  return (
    <Stack gap="sm">
      <Group gap="xs">
        <Badge variant="light" color={own ? 'blue' : 'gray'}>
          {own ? t('aiPrompts.status.adapted') : t('aiPrompts.status.default')}
        </Badge>
      </Group>
      {own?.seedChanged && (
        <Alert color="yellow" variant="light" title={t('aiPrompts.seedChanged.title')}>
          <Stack gap="xs">
            <Text size="sm">{t('aiPrompts.seedChanged.body')}</Text>
            <Text size="xs" c={slate[6]} style={{ whiteSpace: 'pre-wrap' }}>
              {seed.systemPrompt}
              {'\n\n'}
              {seed.userPrompt}
            </Text>
            {canWrite && (
              <Group>
                <Button size="xs" variant="light" loading={busy} onClick={() => void markReviewed()}>
                  {t('aiPrompts.seedChanged.markReviewed')}
                </Button>
              </Group>
            )}
          </Stack>
        </Alert>
      )}
      <Textarea
        {...hint(t('aiPrompts.fields.systemPrompt'), t('aiPrompts.fields.systemPromptHint'))}
        autosize
        minRows={4}
        value={systemPrompt}
        readOnly={!canWrite}
        onChange={(event) => setSystemPrompt(event.currentTarget.value)}
      />
      <Textarea
        {...hint(t('aiPrompts.fields.userPrompt'), t('aiPrompts.fields.userPromptHint'))}
        autosize
        minRows={6}
        value={userPrompt}
        readOnly={!canWrite}
        onChange={(event) => setUserPrompt(event.currentTarget.value)}
      />
      {error && (
        <Alert color="red" variant="light" role="alert">
          {error}
        </Alert>
      )}
      {canWrite && (
        <Group justify="flex-end">
          {own && (
            <Button variant="default" loading={busy} onClick={() => void backToDefault()}>
              {t('aiPrompts.actions.backToDefault')}
            </Button>
          )}
          <Button disabled={!dirty} loading={busy} onClick={() => void save()}>
            {t('aiPrompts.actions.save')}
          </Button>
        </Group>
      )}
    </Stack>
  )
}
