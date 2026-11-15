import { useState } from 'react'
import { Alert, Anchor, Button, Group, Modal, SegmentedControl, Stack, Text, Textarea } from '@mantine/core'
import { Sparkles } from 'lucide-react'
import { useTranslation } from 'react-i18next'
import { Link } from 'react-router-dom'
import { api, ApiError } from '../../api/client'
import type { ListingTextSuggestionRead } from '../../api/types'
import { useFieldHint } from '../../hooks/useFieldHint'
import { SUPPORTED_LANGUAGES, type SupportedLanguage } from '../../i18n'
import { apiRefusalText } from '../../utils/apiRefusal'

function refusalReason(err: unknown): string | undefined {
  if (!(err instanceof ApiError)) return undefined
  const reason = err.details?.reason
  return typeof reason === 'string' ? reason : undefined
}

/**
 * KAN-227 «Text vorschlagen» (D-A-33, FR-A-38) — the Betrieb's own AI
 * provider suggests the listing description from the car's facts. The
 * suggestion opens in an editable field: nothing is stored until the user
 * takes it into the description («In Beschreibung übernehmen»), where it is
 * saved like any text they type — never published by this action. The
 * language is the listing's, the user's own UI language to begin with.
 */
export function ListingTextSuggestion({ stockItemId, onTake }: { stockItemId: string; onTake: (text: string) => Promise<void> }) {
  const { t, i18n } = useTranslation()
  const hint = useFieldHint()
  const uiLanguage: SupportedLanguage = (SUPPORTED_LANGUAGES as readonly string[]).includes(i18n.language)
    ? (i18n.language as SupportedLanguage)
    : 'de'
  const [opened, setOpened] = useState(false)
  const [language, setLanguage] = useState<SupportedLanguage>(uiLanguage)
  const [draft, setDraft] = useState<string | null>(null)
  const [loading, setLoading] = useState(false)
  const [taking, setTaking] = useState(false)
  const [error, setError] = useState<{ text: string; reason?: string } | null>(null)

  const close = () => {
    setOpened(false)
    setDraft(null)
    setError(null)
  }

  const suggest = async () => {
    setLoading(true)
    setError(null)
    try {
      const answer = await api.post<ListingTextSuggestionRead>(
        `/inventory/stock-items/${stockItemId}/listing-text-suggestions`,
        { language },
      )
      setDraft(answer.text)
    } catch (err) {
      setError({ text: apiRefusalText(t, err, 'stockDetail.publishing.suggestion.failed'), reason: refusalReason(err) })
    } finally {
      setLoading(false)
    }
  }

  const take = async () => {
    if (draft === null) return
    setTaking(true)
    try {
      await onTake(draft)
      close()
    } finally {
      setTaking(false)
    }
  }

  return (
    <>
      <Group justify="flex-end">
        <Button size="xs" variant="light" leftSection={<Sparkles size={14} />} onClick={() => setOpened(true)}>
          {t('stockDetail.publishing.suggestion.open')}
        </Button>
      </Group>
      <Modal opened={opened} onClose={close} title={t('stockDetail.publishing.suggestion.title')} size="lg">
        <Stack gap="sm">
          <Text size="sm">{t('stockDetail.publishing.suggestion.intro')}</Text>
          <SegmentedControl
            aria-label={t('stockDetail.publishing.suggestion.language')}
            value={language}
            onChange={(value) => setLanguage(value as SupportedLanguage)}
            data={SUPPORTED_LANGUAGES.map((l) => ({ value: l, label: t(`translations.languages.${l}`) }))}
          />
          {error && (
            <Alert color="red" variant="light" role="alert">
              <Stack gap={4}>
                <Text size="sm">{error.text}</Text>
                {(error.reason === 'ai_not_connected' || error.reason === 'ai_provider_not_configured') && (
                  <Anchor component={Link} to="/administration/integrations" fz="sm">
                    {t('stockDetail.publishing.suggestion.connect')}
                  </Anchor>
                )}
              </Stack>
            </Alert>
          )}
          {draft !== null && (
            <>
              <Textarea
                {...hint(t('stockDetail.publishing.suggestion.draft'), t('stockDetail.publishing.suggestion.review'))}
                autosize
                minRows={6}
                value={draft}
                onChange={(event) => setDraft(event.currentTarget.value)}
              />
            </>
          )}
          <Group justify="flex-end">
            <Button variant="default" onClick={close}>
              {t('stockDetail.publishing.suggestion.discard')}
            </Button>
            <Button variant={draft === null ? 'filled' : 'light'} loading={loading} onClick={() => void suggest()}>
              {draft === null ? t('stockDetail.publishing.suggestion.generate') : t('stockDetail.publishing.suggestion.again')}
            </Button>
            {draft !== null && (
              <Button loading={taking} disabled={!draft.trim()} onClick={() => void take()}>
                {t('stockDetail.publishing.suggestion.take')}
              </Button>
            )}
          </Group>
        </Stack>
      </Modal>
    </>
  )
}
