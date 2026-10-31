import { useEffect, useMemo, useRef, useState } from 'react'
import { Alert, Badge, Button, Card, Group, SimpleGrid, Stack, Text, Textarea } from '@mantine/core'
import { RotateCcw, Send, X } from 'lucide-react'
import { useTranslation } from 'react-i18next'
import { semantic, slate } from '@nexotec/ui-kit'
import { SUPPORTED_LANGUAGES, type SupportedLanguage } from '../../i18n'
import { diffAgainstSeed } from '../../utils/seedDiff'

/**
 * KAN-209 — the Translations panel's editor: one entry (a UI key today; a
 * standard-text seed once KAN-208 plugs in) in DE/FR/IT/EN side by side, each
 * column with its live text, the shipped (seed) text it is diffed against,
 * and a revert. «Publish» sends every language whose draft differs from its
 * live text. Source-agnostic: the caller says how to publish and revert.
 */

export interface EditorLanguageRow {
  value: string
  seedValue: string
  /** True when the live value is the platform's, not the seed. */
  edited: boolean
  version: number
}

export interface PublishOutcome {
  language: SupportedLanguage
  /** null when published; the translated refusal otherwise. */
  error: string | null
}

export interface FourLanguageEditorProps {
  /** Shown as the editor's heading (a key path, a text's code). */
  title: string
  rows: Partial<Record<SupportedLanguage, EditorLanguageRow>>
  canWrite: boolean
  onPublish: (changes: { language: SupportedLanguage; value: string; version: number }[]) => Promise<PublishOutcome[]>
  onRevert: (language: SupportedLanguage, version: number) => Promise<string | null>
  onClose: () => void
}

function SeedDiffView({ seed, value }: { seed: string; value: string }) {
  const { t } = useTranslation()
  const diff = useMemo(() => diffAgainstSeed(seed, value), [seed, value])
  if (diff.same) {
    return (
      <Text size="xs" c={slate[5]}>
        {t('translations.editor.sameAsSeed')}
      </Text>
    )
  }
  return (
    <Stack gap={2}>
      <Text size="xs" c={slate[5]}>
        {t('translations.editor.diffLabel')}
      </Text>
      <Text size="xs" style={{ whiteSpace: 'pre-wrap', wordBreak: 'break-word' }}>
        {diff.prefix}
        {diff.removed && (
          <del
            title={t('translations.editor.removed')}
            style={{ background: semantic.destructive.surface, color: semantic.destructive.text }}
          >
            {diff.removed}
          </del>
        )}
        {diff.added && (
          <ins
            title={t('translations.editor.added')}
            style={{ background: semantic.success.surface, color: semantic.success.text, textDecoration: 'none' }}
          >
            {diff.added}
          </ins>
        )}
        {diff.suffix}
      </Text>
    </Stack>
  )
}

export function FourLanguageEditor({ title, rows, canWrite, onPublish, onRevert, onClose }: FourLanguageEditorProps) {
  const { t } = useTranslation()
  const initialDrafts = useMemo(
    () => Object.fromEntries(SUPPORTED_LANGUAGES.map((lang) => [lang, rows[lang]?.value ?? ''])) as Record<SupportedLanguage, string>,
    [rows],
  )
  const [drafts, setDrafts] = useState(initialDrafts)
  const [errors, setErrors] = useState<Partial<Record<SupportedLanguage, string>>>({})
  const [busy, setBusy] = useState(false)
  const [notice, setNotice] = useState<'published' | 'nothingChanged' | null>(null)

  // Another entry starts afresh. A fresh read of this one (after a publish
  // or a revert) takes the new live text of the languages that changed and
  // keeps every other draft — a language whose publish was refused keeps
  // what was typed, beside its refusal.
  const previous = useRef({ title, initialDrafts })
  useEffect(() => {
    const before = previous.current
    previous.current = { title, initialDrafts }
    if (before.title !== title) {
      setDrafts(initialDrafts)
      setErrors({})
      setNotice(null)
      return
    }
    if (before.initialDrafts === initialDrafts) return
    const moved = SUPPORTED_LANGUAGES.filter((lang) => before.initialDrafts[lang] !== initialDrafts[lang])
    setDrafts((prev) => ({ ...prev, ...Object.fromEntries(moved.map((lang) => [lang, initialDrafts[lang]])) }))
  }, [title, initialDrafts])

  const changed = SUPPORTED_LANGUAGES.filter((lang) => rows[lang] && drafts[lang] !== rows[lang]!.value)

  const publish = async () => {
    if (changed.length === 0) {
      setNotice('nothingChanged')
      return
    }
    setBusy(true)
    setNotice(null)
    try {
      const outcomes = await onPublish(changed.map((language) => ({ language, value: drafts[language], version: rows[language]!.version })))
      const failed = Object.fromEntries(outcomes.filter((o) => o.error).map((o) => [o.language, o.error!]))
      setErrors(failed)
      if (Object.keys(failed).length === 0) setNotice('published')
    } finally {
      setBusy(false)
    }
  }

  const revert = async (language: SupportedLanguage) => {
    const row = rows[language]
    if (!row) return
    setBusy(true)
    setNotice(null)
    try {
      const error = await onRevert(language, row.version)
      setErrors((prev) => ({ ...prev, [language]: error ?? undefined }))
      if (!error) setNotice('published')
    } finally {
      setBusy(false)
    }
  }

  return (
    <Card component="section" withBorder padding="lg" radius="md" aria-label={title}>
      <Stack gap="md">
        <Group justify="space-between" align="flex-start" wrap="nowrap">
          <Stack gap={0}>
            <Text size="xs" c={slate[5]}>
              {t('translations.editor.keyLabel')}
            </Text>
            <Text fw={600} ff="monospace" style={{ wordBreak: 'break-all' }}>
              {title}
            </Text>
          </Stack>
          <Button variant="subtle" color="gray" leftSection={<X size={14} />} onClick={onClose}>
            {t('translations.editor.close')}
          </Button>
        </Group>

        {!canWrite && (
          <Alert color="gray" variant="light">
            {t('translations.editor.readOnly')}
          </Alert>
        )}
        {canWrite && (
          <Text size="xs" c={slate[5]}>
            {t('translations.editor.placeholdersHint', { example: '{{count, number}}' })}
          </Text>
        )}

        <SimpleGrid cols={{ base: 1, sm: 2, lg: 4 }} spacing="md">
          {SUPPORTED_LANGUAGES.map((language) => {
            const row = rows[language]
            const label = t(`translations.languages.${language}`)
            if (!row) {
              return (
                <Stack key={language} gap={4}>
                  <Text size="sm" fw={600}>
                    {label}
                  </Text>
                  <Text size="sm" c={slate[4]} fs="italic">
                    {t('translations.editor.noRow')}
                  </Text>
                </Stack>
              )
            }
            return (
              <Stack key={language} gap={6}>
                <Group justify="space-between" gap="xs">
                  <Text size="sm" fw={600}>
                    {label}
                  </Text>
                  <Badge size="sm" variant="light" color={row.edited ? 'grape' : 'gray'}>
                    {row.edited ? t('translations.status.platform') : t('translations.status.seed')}
                  </Badge>
                </Group>
                <Textarea
                  aria-label={label}
                  value={drafts[language]}
                  onChange={(event) => {
                    const value = event.currentTarget.value
                    setDrafts((prev) => ({ ...prev, [language]: value }))
                  }}
                  readOnly={!canWrite}
                  autosize
                  minRows={2}
                  maxRows={10}
                  error={errors[language]}
                />
                <SeedDiffView seed={row.seedValue} value={drafts[language]} />
                <Text size="xs" c={slate[5]} style={{ whiteSpace: 'pre-wrap', wordBreak: 'break-word' }}>
                  {t('translations.editor.seedLabel')}: {row.seedValue}
                </Text>
                {canWrite && row.edited && (
                  <Button
                    size="compact-xs"
                    variant="subtle"
                    color="gray"
                    leftSection={<RotateCcw size={12} />}
                    onClick={() => void revert(language)}
                    disabled={busy}
                  >
                    {t('translations.editor.revert')}
                  </Button>
                )}
              </Stack>
            )
          })}
        </SimpleGrid>

        {notice && (
          <Alert color={notice === 'published' ? 'green' : 'gray'} variant="light">
            {t(`translations.editor.${notice}`)}
          </Alert>
        )}

        {canWrite && (
          <Group justify="flex-end" gap="sm">
            <Button variant="default" onClick={() => setDrafts(initialDrafts)} disabled={busy || changed.length === 0}>
              {t('translations.editor.discard')}
            </Button>
            <Button leftSection={<Send size={14} />} onClick={() => void publish()} loading={busy}>
              {t('translations.editor.publish')}
            </Button>
          </Group>
        )}
      </Stack>
    </Card>
  )
}
