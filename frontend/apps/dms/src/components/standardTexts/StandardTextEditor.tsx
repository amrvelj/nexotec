import { useEffect, useMemo, useRef, useState } from 'react'
import { Alert, Button, Group, Modal, Paper, Select, SimpleGrid, Stack, Switch, Tabs, Text, TextInput, Textarea } from '@mantine/core'
import { useMutation, useQuery } from '@tanstack/react-query'
import { useTranslation } from 'react-i18next'
import { Badge } from '@nexotec/ui-kit'
import { ApiError, api } from '../../api/client'
import type {
  StandardTextAppliesTo,
  StandardTextCategory,
  StandardTextCreate,
  StandardTextPreviewRead,
  StandardTextRead,
  StandardTextSlot,
  StandardTextTargetRead,
  StandardTextUpdate,
} from '../../api/types'
import { useIdempotencyKey } from '../../hooks/useIdempotencyKey'
import type { SupportedLanguage } from '../../i18n'
import { apiRefusalText } from '../../utils/apiRefusal'
import { TEXT_LANGUAGES, placeholderKey, type TextEntry } from './standardTextModel'

interface Draft {
  title: string
  body: string
  isDefault: boolean
  active: boolean
  templateApproved: boolean
  providerTemplateId: string
}

const PREVIEW_DELAY_MS = 400

function draftOf(row: StandardTextRead | null): Draft {
  return {
    title: row?.title ?? '',
    body: row?.body ?? '',
    isDefault: row?.isDefault ?? false,
    active: row?.active ?? true,
    templateApproved: row?.templateApproved ?? false,
    providerTemplateId: row?.providerTemplateId ?? '',
  }
}

function useDebounced<T>(value: T, delay: number): T {
  const [debounced, setDebounced] = useState(value)
  useEffect(() => {
    const timer = setTimeout(() => setDebounced(value), delay)
    return () => clearTimeout(timer)
  }, [value, delay])
  return debounced
}

interface PlaceholderProblem {
  token: string
  problem: string
}

export interface StandardTextEditorProps {
  opened: boolean
  onClose: () => void
  dealershipId: string
  category: StandardTextCategory
  /** The entry to edit; null opens the editor for a new text of the Betrieb. */
  entry: TextEntry | null
  /** Where texts of this category may be used, with the placeholders each offers. */
  targets: StandardTextTargetRead[]
  /** The caller may write this category (the manager, or a delegate of it — FR-A-28). */
  writable: boolean
  uiLanguage: SupportedLanguage
  /** A save landed: the page refetches the library; the editor follows the code. */
  onSaved: (code: string) => void
}

/**
 * KAN-208 — the standard-text editor (FR-A-07): one tab per language (one
 * row per language, never four columns), the placeholders the text's target
 * offers and nothing else, and a preview on sample data rendered by the same
 * server renderer a document uses — a token it cannot resolve shows here as
 * it would fail the document (A-RULE-4). A seeded text is a template
 * («Vorlage — bitte prüfen», FR-A-08): saving it creates the Betrieb's
 * adaptation, the seed itself stays Nexotec's (FR-A-29); a changed seed asks
 * for a review of the adaptation.
 */
export function StandardTextEditor({ opened, onClose, dealershipId, category, entry, targets, writable, uiLanguage, onSaved }: StandardTextEditorProps) {
  const { t } = useTranslation()
  const [language, setLanguage] = useState<SupportedLanguage>(uiLanguage)
  const [appliesTo, setAppliesTo] = useState<StandardTextAppliesTo | null>(entry?.appliesTo ?? targets[0]?.appliesTo ?? null)
  const [slot, setSlot] = useState<StandardTextSlot | null>(entry?.slot ?? targets[0]?.slots[0] ?? null)
  const [code, setCode] = useState<string | null>(entry?.code ?? null)
  const [drafts, setDrafts] = useState<Partial<Record<SupportedLanguage, Draft>>>({})
  const [error, setError] = useState<string | null>(null)
  const [saved, setSaved] = useState(false)
  const textarea = useRef<HTMLTextAreaElement>(null)
  const idempotency = useIdempotencyKey()
  const previewKey = useIdempotencyKey()

  const isNew = code === null
  const version = entry?.languages[language] ?? { seed: null, own: null }
  const loaded = version.own ?? version.seed
  const draft = drafts[language] ?? draftOf(loaded)
  const target = targets.find((candidate) => candidate.appliesTo === appliesTo) ?? null
  const isWhatsapp = category === 'whatsapp'

  const setDraft = (patch: Partial<Draft>) => {
    setSaved(false)
    setDrafts((current) => ({ ...current, [language]: { ...draft, ...patch } }))
  }

  const dirty = useMemo(() => {
    const base = draftOf(loaded)
    return (Object.keys(base) as (keyof Draft)[]).some((field) => base[field] !== draft[field])
  }, [draft, loaded])
  // A seed with no adaptation yet is saved as the Betrieb's own wording even unchanged.
  const canSave = writable && draft.title.trim() !== '' && draft.body.trim() !== '' && (dirty || (version.own === null && version.seed !== null)) && appliesTo !== null && slot !== null

  const insertPlaceholder = (token: string) => {
    const element = textarea.current
    const insert = `{{${token}}}`
    const start = element?.selectionStart ?? draft.body.length
    const end = element?.selectionEnd ?? draft.body.length
    setDraft({ body: draft.body.slice(0, start) + insert + draft.body.slice(end) })
    requestAnimationFrame(() => {
      element?.focus()
      element?.setSelectionRange(start + insert.length, start + insert.length)
    })
  }

  const previewBody = useDebounced(draft.body, PREVIEW_DELAY_MS)
  const preview = useQuery({
    queryKey: ['standard-text-preview', dealershipId, category, appliesTo, language, previewBody],
    queryFn: () => {
      const request = { category, appliesTo: appliesTo as StandardTextAppliesTo, language, body: previewBody }
      return api.post<StandardTextPreviewRead>(`/dealerships/${dealershipId}/standard-text-previews`, request, previewKey.headers(request))
    },
    enabled: opened && appliesTo !== null && previewBody.trim() !== '',
    retry: false,
  })
  const previewProblems: PlaceholderProblem[] =
    preview.error instanceof ApiError && Array.isArray(preview.error.details?.placeholders)
      ? (preview.error.details.placeholders as PlaceholderProblem[])
      : []

  const afterSave = (row: StandardTextRead) => {
    setError(null)
    setSaved(true)
    setCode(row.code)
    setDrafts((current) => ({ ...current, [language]: draftOf(row) }))
    idempotency.renew()
    onSaved(row.code)
  }

  const save = useMutation({
    mutationFn: async (): Promise<StandardTextRead> => {
      const own = version.own
      const whatsapp = isWhatsapp ? { templateApproved: draft.templateApproved, providerTemplateId: draft.providerTemplateId.trim() || null } : {}
      if (own) {
        const body: StandardTextUpdate = { title: draft.title, body: draft.body, isDefault: draft.isDefault, active: draft.active, ...whatsapp }
        return api.patch<StandardTextRead>(`/dealerships/${dealershipId}/standard-texts/${category}/${own.id}`, body, { 'If-Match': String(own.version) })
      }
      const body: StandardTextCreate = {
        language,
        title: draft.title,
        body: draft.body,
        isDefault: draft.isDefault,
        active: draft.active,
        ...(code ? { code } : { appliesTo: appliesTo as StandardTextAppliesTo, slot: slot as StandardTextSlot }),
        ...whatsapp,
      }
      return api.post<StandardTextRead>(`/dealerships/${dealershipId}/standard-texts/${category}`, body, idempotency.headers(body))
    },
    onSuccess: afterSave,
    onError: (err) => {
      setSaved(false)
      setError(apiRefusalText(t, err, 'standardTexts.errors.saveFailed'))
    },
  })

  const markReviewed = useMutation({
    mutationFn: (own: StandardTextRead) =>
      api.patch<StandardTextRead>(`/dealerships/${dealershipId}/standard-texts/${category}/${own.id}`, { seedReviewed: true } satisfies StandardTextUpdate, {
        'If-Match': String(own.version),
      }),
    onSuccess: (row) => {
      setError(null)
      onSaved(row.code)
    },
    onError: (err) => setError(apiRefusalText(t, err, 'standardTexts.errors.saveFailed')),
  })

  const remove = useMutation({
    mutationFn: (own: StandardTextRead) =>
      api.delete<void>(`/dealerships/${dealershipId}/standard-texts/${category}/${own.id}`, { 'If-Match': String(own.version) }),
    onSuccess: () => {
      setError(null)
      setSaved(false)
      setDrafts((current) => ({ ...current, [language]: undefined }))
      if (code) onSaved(code)
    },
    onError: (err) => setError(apiRefusalText(t, err, 'standardTexts.errors.saveFailed')),
  })

  const ownRow = version.own
  const busy = save.isPending || markReviewed.isPending || remove.isPending

  return (
    <Modal
      opened={opened}
      onClose={onClose}
      size="80rem"
      title={isNew ? t('standardTexts.editor.newTitle') : t('standardTexts.editor.title')}
      closeButtonProps={{ 'aria-label': t('standardTexts.editor.close') }}
    >
      <Stack gap="md">
        <SimpleGrid cols={{ base: 1, sm: 2 }}>
          <Select
            label={t('standardTexts.fields.appliesTo')}
            data={targets.map((candidate) => ({ value: candidate.appliesTo, label: t(`standardTexts.appliesTo.${candidate.appliesTo}`) }))}
            value={appliesTo}
            onChange={(value) => {
              const next = targets.find((candidate) => candidate.appliesTo === value)
              setAppliesTo((value as StandardTextAppliesTo) ?? null)
              setSlot(next?.slots[0] ?? null)
            }}
            disabled={!isNew}
            allowDeselect={false}
          />
          <Select
            label={t('standardTexts.fields.slot')}
            data={(target?.slots ?? []).map((value) => ({ value, label: t(`standardTexts.slot.${value}`) }))}
            value={slot}
            onChange={(value) => setSlot((value as StandardTextSlot) ?? null)}
            disabled={!isNew}
            allowDeselect={false}
          />
        </SimpleGrid>

        <Tabs value={language} onChange={(value) => value && setLanguage(value as SupportedLanguage)} keepMounted={false}>
          <Tabs.List>
            {TEXT_LANGUAGES.map((lang) => {
              const v = entry?.languages[lang]
              return (
                <Tabs.Tab key={lang} value={lang}>
                  {t(`standardTexts.language.${lang}`)}
                  {v && !v.own && !v.seed ? ` · ${t('standardTexts.editor.missingLanguage')}` : ''}
                </Tabs.Tab>
              )
            })}
          </Tabs.List>
        </Tabs>

        {!ownRow && version.seed && (
          <Alert color="yellow" title={t('standardTexts.badge.template')}>
            {writable ? t('standardTexts.editor.templateHint') : t('standardTexts.editor.templateHintReadOnly')}
          </Alert>
        )}
        {ownRow?.seedChanged && version.seed && (
          <Alert color="orange" title={t('standardTexts.editor.seedChangedTitle')} role="status">
            <Stack gap="xs">
              <Text fz="sm">{t('standardTexts.editor.seedChangedHint')}</Text>
              <Paper withBorder p="sm" radius="md">
                <Text fz="sm" style={{ whiteSpace: 'pre-line' }} aria-label={t('standardTexts.editor.currentTemplate')}>
                  {version.seed.body}
                </Text>
              </Paper>
              {writable && (
                <Group>
                  <Button size="xs" variant="light" onClick={() => markReviewed.mutate(ownRow)} loading={markReviewed.isPending}>
                    {t('standardTexts.editor.markReviewed')}
                  </Button>
                </Group>
              )}
            </Stack>
          </Alert>
        )}

        <SimpleGrid cols={{ base: 1, md: 2 }} spacing="lg">
          <Stack gap="sm">
            <TextInput
              label={t('standardTexts.fields.title')}
              value={draft.title}
              onChange={(e) => setDraft({ title: e.currentTarget.value })}
              readOnly={!writable}
              maxLength={200}
              required
            />
            <Textarea
              ref={textarea}
              label={t('standardTexts.fields.body')}
              value={draft.body}
              onChange={(e) => setDraft({ body: e.currentTarget.value })}
              readOnly={!writable}
              autosize
              minRows={8}
              required
            />
            {writable && target && (
              <Stack gap={4}>
                <Text fz="sm" fw={500}>
                  {t('standardTexts.editor.placeholders')}
                </Text>
                <Group gap={6} role="group" aria-label={t('standardTexts.editor.placeholders')}>
                  {target.placeholders.map((token) => (
                    <Button key={token} size="compact-xs" variant="default" onClick={() => insertPlaceholder(token)} title={`{{${token}}}`}>
                      {t(placeholderKey(token))}
                    </Button>
                  ))}
                </Group>
              </Stack>
            )}
            <Group>
              <Switch label={t('standardTexts.fields.isDefault')} checked={draft.isDefault} onChange={(e) => setDraft({ isDefault: e.currentTarget.checked })} disabled={!writable} />
              <Switch label={t('standardTexts.fields.active')} checked={draft.active} onChange={(e) => setDraft({ active: e.currentTarget.checked })} disabled={!writable} />
            </Group>
            {isWhatsapp && (
              <Stack gap="xs">
                <Switch
                  label={t('standardTexts.fields.templateApproved')}
                  checked={draft.templateApproved}
                  onChange={(e) => setDraft({ templateApproved: e.currentTarget.checked })}
                  disabled={!writable}
                />
                <TextInput
                  label={t('standardTexts.fields.providerTemplateId')}
                  value={draft.providerTemplateId}
                  onChange={(e) => setDraft({ providerTemplateId: e.currentTarget.value })}
                  readOnly={!writable}
                  maxLength={200}
                />
                <Badge tone={draft.templateApproved ? 'success' : 'warning'} dot>
                  {draft.templateApproved ? t('standardTexts.whatsapp.approved') : t('standardTexts.whatsapp.notApproved')}
                </Badge>
              </Stack>
            )}
          </Stack>

          <Stack gap="xs">
            <Text fz="sm" fw={500}>
              {t('standardTexts.editor.preview')}
            </Text>
            <Text fz="xs" c="dimmed">
              {t('standardTexts.editor.previewHint')}
            </Text>
            <Paper withBorder p="md" radius="md" aria-label={t('standardTexts.editor.preview')} role="region">
              {previewBody.trim() === '' ? (
                <Text fz="sm" c="dimmed">
                  {t('standardTexts.editor.previewEmpty')}
                </Text>
              ) : previewProblems.length > 0 ? (
                <Stack gap={4} role="alert">
                  <Text fz="sm" fw={500}>
                    {t('standardTexts.editor.previewFailed')}
                  </Text>
                  {previewProblems.map((problem) => (
                    <Text key={`${problem.problem}-${problem.token}`} fz="sm">
                      {t(`standardTexts.problem.${problem.problem}`, { token: problem.token ? `{{${problem.token}}}` : '' })}
                    </Text>
                  ))}
                </Stack>
              ) : preview.isError ? (
                <Text fz="sm" role="alert">
                  {apiRefusalText(t, preview.error, 'standardTexts.errors.previewFailed')}
                </Text>
              ) : (
                <Text fz="sm" style={{ whiteSpace: 'pre-line' }} data-testid="standard-text-preview">
                  {preview.data?.text ?? t('common.loading')}
                </Text>
              )}
            </Paper>
          </Stack>
        </SimpleGrid>

        {error && (
          <Alert color="red" role="alert" withCloseButton closeButtonLabel={t('common.dismiss')} onClose={() => setError(null)}>
            {error}
          </Alert>
        )}

        <Group justify="space-between">
          <Group>
            {writable && ownRow && (
              <Button variant="subtle" color="red" onClick={() => remove.mutate(ownRow)} loading={remove.isPending} disabled={busy}>
                {ownRow.overriddenFromId ? t('standardTexts.editor.resetToTemplate') : t('standardTexts.editor.deleteLanguage')}
              </Button>
            )}
          </Group>
          <Group>
            {saved && !dirty && (
              <Text c="dimmed" fz="sm" role="status">
                {t('standardTexts.editor.saved')}
              </Text>
            )}
            <Button variant="default" onClick={onClose}>
              {t('common.cancel')}
            </Button>
            {writable && (
              <Button onClick={() => save.mutate()} disabled={!canSave || busy} loading={save.isPending}>
                {!ownRow && version.seed ? t('standardTexts.editor.adapt') : t('common.save')}
              </Button>
            )}
          </Group>
        </Group>
      </Stack>
    </Modal>
  )
}
