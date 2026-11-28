import { useEffect, useState } from 'react'
import { Group, Paper, Radio, Select, Stack, Text, TextInput } from '@mantine/core'
import { Phone } from 'lucide-react'
import { useTranslation } from 'react-i18next'
import { FormDialog, slate } from '@nexotec/ui-kit'
import { api } from '../../../../api/client'
import type {
  AnswerChannel,
  AnswerResult,
  ServiceOrderAnswersCreate,
  ServiceOrderJobRead,
  ServiceOrderRead,
} from '../../../../api/types'
import { useIdempotencyKey } from '../../../../hooks/useIdempotencyKey'
import { formatCurrencyChf } from '../../../../utils/format'
import { decimal } from '../../serviceOrderOptions'
import { RefusalAlert } from './RefusalAlert'

const CHANNELS: AnswerChannel[] = ['desk', 'phone', 'message']

type Choice = 'keep' | AnswerResult

interface Draft {
  choice: Choice
  note: string
  hoursBilled: 'billed' | 'notBilled' | null
}

/** The answers table 3.2 offers a job in its state; «keep» leaves it as it is. */
function choicesFor(job: ServiceOrderJobRead): Choice[] {
  if (job.answerState === 'draft' || job.answerState === 'waiting') return ['keep', 'accepted', 'declined']
  if (job.answerState === 'accepted') return ['keep', 'declined']
  return ['keep']
}

/** Declining a job with clocked time — accepted, or waiting again with work running — asks
 * whether the hours are billed (ruling 48, check 8). */
function asksHours(job: ServiceOrderJobRead, draft: Draft): boolean {
  return draft.choice === 'declined' && Boolean(job.timeClocked)
}

/**
 * D8 «Antwort erfassen» (KAN-811; the slice page, table 3.2): the customer
 * the answer is from, how it came (am Schalter, per Telefon, per Nachricht),
 * and per job its amount and the answer — angenommen or abgelehnt, a note
 * required on a decline. A stopped job is not offered (decision 54). The
 * rules are the server's; a refusal is said in one sentence.
 */
export function AnswerDialog({
  opened,
  order,
  onClose,
  onSaved,
  onReload,
}: {
  opened: boolean
  order: ServiceOrderRead
  onClose: () => void
  onSaved: (order: ServiceOrderRead) => void
  onReload: () => void
}) {
  const { t } = useTranslation()
  const idempotency = useIdempotencyKey()
  const jobs = order.jobs.filter((job) => !job.stopped)
  const [channel, setChannel] = useState<AnswerChannel>('desk')
  const [drafts, setDrafts] = useState<Record<string, Draft>>({})
  const [showMissing, setShowMissing] = useState(false)
  const [submitting, setSubmitting] = useState(false)
  const [error, setError] = useState<unknown>(null)

  useEffect(() => {
    if (opened) {
      setChannel('desk')
      setDrafts({})
      setShowMissing(false)
      setError(null)
    }
  }, [opened])

  const draftOf = (jobId: string): Draft => drafts[jobId] ?? { choice: 'keep', note: '', hoursBilled: null }
  const update = (jobId: string, change: Partial<Draft>) =>
    setDrafts((prev) => ({ ...prev, [jobId]: { ...draftOf(jobId), ...change } }))

  const chosen = jobs.filter((job) => draftOf(job.id).choice !== 'keep')
  const noteMissing = (job: ServiceOrderJobRead) => draftOf(job.id).choice === 'declined' && draftOf(job.id).note.trim() === ''
  const hoursMissing = (job: ServiceOrderJobRead) => asksHours(job, draftOf(job.id)) && draftOf(job.id).hoursBilled === null

  const submit = async () => {
    // Mirrors of the server's refusals, said at the field — never a held-back button.
    if (chosen.length === 0 || chosen.some(noteMissing) || chosen.some(hoursMissing)) {
      setShowMissing(true)
      return
    }
    setSubmitting(true)
    setError(null)
    try {
      const body: ServiceOrderAnswersCreate = {
        channel,
        jobs: chosen.map((job) => {
          const draft = draftOf(job.id)
          const entry: ServiceOrderAnswersCreate['jobs'][number] = { jobId: job.id, result: draft.choice as AnswerResult }
          if (draft.note.trim() !== '') entry.note = draft.note.trim()
          if (asksHours(job, draft)) entry.hoursBilled = draft.hoursBilled === 'billed'
          return entry
        }),
      }
      const path = `/service-orders/${order.id}/answers`
      const updated = await api.post<ServiceOrderRead>(path, body, {
        'If-Match': String(order.version),
        ...idempotency.headers([path, body]),
      })
      idempotency.renew()
      onSaved(updated)
    } catch (err) {
      setError(err)
    } finally {
      setSubmitting(false)
    }
  }

  const amountLine = (job: ServiceOrderJobRead) =>
    job.amount
      ? t('serviceOrder.answer.amount', {
          net: formatCurrencyChf(decimal(job.amount.net)),
          gross: formatCurrencyChf(decimal(job.amount.gross)),
        })
      : t('serviceOrder.answer.noPrice')

  return (
    <FormDialog
      opened={opened}
      onClose={onClose}
      title={t('serviceOrder.answer.title')}
      onSubmit={submit}
      submitLabel={t('serviceOrder.answer.save')}
      cancelLabel={t('common.cancel')}
      submitting={submitting}
      size="lg"
    >
      <Stack gap="md">
        <Paper bg={slate[1]} p="sm" radius="md">
          <Group gap="xs" wrap="nowrap">
            <Phone size={16} aria-hidden />
            <Text size="sm" fw={600} data-testid="answer-contact">
              {order.contact.label}
            </Text>
          </Group>
        </Paper>
        <Select
          label={t('serviceOrder.answer.channelLabel')}
          data={CHANNELS.map((value) => ({ value, label: t(`serviceOrder.answer.channel.${value}`) }))}
          value={channel}
          onChange={(value) => value && setChannel(value as AnswerChannel)}
          allowDeselect={false}
        />
        {jobs.length === 0 && (
          <Text size="sm" c="dimmed">
            {t('serviceOrder.answer.noJobs')}
          </Text>
        )}
        {jobs.map((job) => {
          const draft = draftOf(job.id)
          const choices = choicesFor(job)
          return (
            <Stack key={job.id} gap={6} data-testid={`answer-job-${job.id}`}>
              <Group justify="space-between" wrap="nowrap" align="flex-start">
                <div>
                  <Text size="sm" fw={600}>
                    {job.name}
                  </Text>
                  <Text size="xs" c="dimmed">
                    {amountLine(job)}
                  </Text>
                </div>
                <Select
                  aria-label={t('serviceOrder.answer.resultFor', { name: job.name })}
                  w={200}
                  data={choices.map((value) => ({
                    value,
                    label: value === 'keep' ? t(`serviceOrder.answerState.${job.answerState}`) : t(`serviceOrder.answer.result.${value}`),
                  }))}
                  value={draft.choice}
                  onChange={(value) => value && update(job.id, { choice: value as Choice, hoursBilled: null })}
                  allowDeselect={false}
                  disabled={choices.length === 1}
                />
              </Group>
              {choices.length > 1 && (
                <TextInput
                  aria-label={t('serviceOrder.answer.noteFor', { name: job.name })}
                  placeholder={t('serviceOrder.answer.notePlaceholder')}
                  value={draft.note}
                  onChange={(e) => update(job.id, { note: e.currentTarget.value })}
                  maxLength={1000}
                  error={showMissing && noteMissing(job) ? t('serviceOrder.answer.noteMissing') : undefined}
                />
              )}
              {asksHours(job, draft) && (
                <Radio.Group
                  label={t('serviceOrder.answer.hoursQuestion')}
                  value={draft.hoursBilled ?? ''}
                  onChange={(value) => update(job.id, { hoursBilled: value as Draft['hoursBilled'] })}
                  error={showMissing && hoursMissing(job) ? t('serviceOrder.answer.hoursMissing') : undefined}
                >
                  <Stack gap={4} mt={4}>
                    <Radio value="billed" label={t('serviceOrder.answer.hoursBilled')} />
                    <Radio value="notBilled" label={t('serviceOrder.answer.hoursNotBilled')} />
                  </Stack>
                </Radio.Group>
              )}
            </Stack>
          )
        })}
        {showMissing && chosen.length === 0 && jobs.length > 0 && (
          <Text size="sm" c="red" role="alert">
            {t('serviceOrder.answer.nothingChosen')}
          </Text>
        )}
        {error !== null && (
          <RefusalAlert
            error={error}
            fallbackKey="serviceOrder.saveError"
            onReload={() => {
              onReload()
              onClose()
            }}
          />
        )}
      </Stack>
    </FormDialog>
  )
}
