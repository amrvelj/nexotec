import { useEffect, useState } from 'react'
import { Alert, MultiSelect, SimpleGrid, Stack, Text, TextInput } from '@mantine/core'
import { CircleAlert } from 'lucide-react'
import { useTranslation } from 'react-i18next'
import { CounterTextarea, FormDialog } from '@nexotec/ui-kit'
import { api } from '../../../api/client'
import type { BookingOutcomeRead } from '../../../api/types'
import { useFieldHint } from '../../../hooks/useFieldHint'
import { useIdempotencyKey } from '../../../hooks/useIdempotencyKey'
import { apiRefusalText } from '../../../utils/apiRefusal'
import { zurichInstant } from '../plannerTime'
import type { PlannerOption } from './BookingDialog'

const NOTE_LENGTH = 200

interface BlockResourceDialogProps {
  opened: boolean
  /** The day shown, YYYY-MM-DD: the dialog starts on it. */
  day: string
  planningPath: string
  people: PlannerOption[]
  workplaces: PlannerOption[]
  onClose: () => void
  /** After a block: the day is read again; `marked` is how many bookings it marked «neu einplanen». */
  onSaved: (marked: number) => void
}

/**
 * KAN-809 — PL4, block a resource: people and/or workplaces, from, to, and a
 * note that is optional (at most 200 characters; Anto, 2026-11-25 — the drawn
 * «Grund *», «Fahrzeug» and «Wöchentlich wiederholen» are not built, his
 * answer of 2026-11-26). A block is always possible; what it hits is marked
 * «neu einplanen» by the server, and the page shows it.
 */
export function BlockResourceDialog({ opened, day, planningPath, people, workplaces, onClose, onSaved }: BlockResourceDialogProps) {
  const { t } = useTranslation()
  const [note, setNote] = useState('')
  const [fromDate, setFromDate] = useState(day)
  const [fromTime, setFromTime] = useState('07:00')
  const [toDate, setToDate] = useState(day)
  const [toTime, setToTime] = useState('18:00')
  const [personIds, setPersonIds] = useState<string[]>([])
  const [workplaceIds, setWorkplaceIds] = useState<string[]>([])
  const [formError, setFormError] = useState<string | null>(null)
  const [refusal, setRefusal] = useState<string | null>(null)
  const [submitting, setSubmitting] = useState(false)
  const idempotency = useIdempotencyKey()
  const hint = useFieldHint()

  useEffect(() => {
    if (!opened) return
    setNote('')
    setFromDate(day)
    setFromTime('07:00')
    setToDate(day)
    setToTime('18:00')
    setPersonIds([])
    setWorkplaceIds([])
    setFormError(null)
    setRefusal(null)
    idempotency.renew()
  }, [opened, day, idempotency])

  const submit = async () => {
    const resourceIds = [...personIds, ...workplaceIds]
    if (resourceIds.length === 0) return setFormError(t('planner.booking.needsResource'))
    if (!fromDate || !fromTime || !toDate || !toTime) return setFormError(t('planner.booking.timeRequired'))
    const startsAt = zurichInstant(fromDate, fromTime)
    const endsAt = zurichInstant(toDate, toTime)
    if (new Date(endsAt) <= new Date(startsAt)) return setFormError(t('planner.booking.endAfterStart'))
    setFormError(null)
    setRefusal(null)
    setSubmitting(true)
    try {
      const outcome = await api.post<BookingOutcomeRead>(
        `${planningPath}/blocks`,
        { resourceIds, startsAt, endsAt, note: note.trim() === '' ? null : note.trim() },
        idempotency.headers(),
      )
      idempotency.renew()
      onSaved(outcome.markedForReplanning.length)
      onClose()
    } catch (err) {
      setRefusal(apiRefusalText(t, err, 'planner.block.failed'))
    } finally {
      setSubmitting(false)
    }
  }

  return (
    <FormDialog
      opened={opened}
      onClose={onClose}
      title={t('planner.block.title')}
      onSubmit={submit}
      submitLabel={t('planner.block.submit')}
      cancelLabel={t('planner.block.cancel')}
      submitting={submitting}
      size="lg"
    >
      <Stack gap="sm">
        <CounterTextarea
          {...hint(t('planner.fields.note'), t('planner.fields.noteHint'))}
          value={note}
          onChange={setNote}
          maxLength={NOTE_LENGTH}
        />
        <SimpleGrid cols={2} spacing="sm">
          <TextInput type="date" label={`${t('planner.fields.from')} – ${t('planner.fields.date')}`} value={fromDate} onChange={(e) => setFromDate(e.currentTarget.value)} required />
          <TextInput type="time" label={`${t('planner.fields.from')} – ${t('planner.fields.time')}`} value={fromTime} onChange={(e) => setFromTime(e.currentTarget.value)} required />
          <TextInput type="date" label={`${t('planner.fields.to')} – ${t('planner.fields.date')}`} value={toDate} onChange={(e) => setToDate(e.currentTarget.value)} required />
          <TextInput type="time" label={`${t('planner.fields.to')} – ${t('planner.fields.time')}`} value={toTime} onChange={(e) => setToTime(e.currentTarget.value)} required />
        </SimpleGrid>
        <SimpleGrid cols={2} spacing="sm">
          <MultiSelect label={t('planner.fields.people')} data={people} value={personIds} onChange={setPersonIds} placeholder={personIds.length === 0 ? t('planner.fields.none') : undefined} searchable />
          <MultiSelect label={t('planner.fields.workplaces')} data={workplaces} value={workplaceIds} onChange={setWorkplaceIds} placeholder={workplaceIds.length === 0 ? t('planner.fields.none') : undefined} searchable />
        </SimpleGrid>
        {formError && (
          <Text size="sm" c="red" role="alert">
            {formError}
          </Text>
        )}
        {refusal && (
          <Alert color="red" variant="light" icon={<CircleAlert size={16} />} role="alert">
            {refusal}
          </Alert>
        )}
      </Stack>
    </FormDialog>
  )
}
