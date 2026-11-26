import { useEffect, useState } from 'react'
import { Alert, Button, Group, List, MultiSelect, Select, SimpleGrid, Stack, Text, TextInput } from '@mantine/core'
import { CircleAlert, RotateCcw, TriangleAlert } from 'lucide-react'
import { useTranslation } from 'react-i18next'
import { FormDialog } from '@nexotec/ui-kit'
import { api, ApiError } from '../../../api/client'
import type { BookingOutcomeRead, BookingRead, ConfirmedWarningWrite } from '../../../api/types'
import { apiRefusalText, isStaleVersion } from '../../../utils/apiRefusal'
import { wallClock, zurichDateTime, zurichInstant } from '../plannerTime'
import { unconfirmedWarnings, warningText } from '../warningText'

export interface PlannerOption {
  value: string
  label: string
}

interface BookingDialogProps {
  booking: BookingRead | null
  /** `/dealerships/{id}/planning` */
  planningPath: string
  people: PlannerOption[]
  workplaces: PlannerOption[]
  resourceLabels: ReadonlyMap<string, string>
  onClose: () => void
  /** After a change or a release: the day is read again. */
  onSaved: () => void
}

/**
 * KAN-809 — PL2, the booking dialog: the one way in this ticket to change a
 * booking (Anto, 2026-11-26: change and release only; work and slots are made
 * from the order, KAN-812). People, workplace, from, to; release with a
 * confirmation. Every rule is the server's: a refusal is said in one
 * sentence; a warning is listed sentence by sentence and can be overruled —
 * the dialog says that each one is recorded with the name and the time, and
 * sends back exactly the warnings shown (per warning, never per code). A
 * change to any field after that withdraws the confirmation.
 */
export function BookingDialog({ booking, planningPath, people, workplaces, resourceLabels, onClose, onSaved }: BookingDialogProps) {
  const { t } = useTranslation()
  const isWork = booking?.kind === 'work'
  const isBlock = booking?.kind === 'block'
  const [fromDate, setFromDate] = useState('')
  const [fromTime, setFromTime] = useState('')
  const [toDate, setToDate] = useState('')
  const [toTime, setToTime] = useState('')
  const [personIds, setPersonIds] = useState<string[]>([])
  const [workplaceIds, setWorkplaceIds] = useState<string[]>([])
  const [warnings, setWarnings] = useState<ConfirmedWarningWrite[] | null>(null)
  const [refusal, setRefusal] = useState<{ text: string; stale: boolean } | null>(null)
  const [formError, setFormError] = useState<string | null>(null)
  const [confirmRelease, setConfirmRelease] = useState(false)
  const [submitting, setSubmitting] = useState(false)

  useEffect(() => {
    if (!booking) return
    const from = wallClock(booking.startsAt)
    const to = wallClock(booking.endsAt)
    setFromDate(from.date)
    setFromTime(from.time)
    setToDate(to.date)
    setToTime(to.time)
    setPersonIds(booking.resources.filter((r) => r.kind === 'person').map((r) => r.resourceId))
    setWorkplaceIds(booking.resources.filter((r) => r.kind === 'workplace').map((r) => r.resourceId))
    setWarnings(null)
    setRefusal(null)
    setFormError(null)
    setConfirmRelease(false)
  }, [booking])

  // The booking's own resources stay choosable, even one no longer listed at the location.
  const withOwn = (options: PlannerOption[], kind: 'person' | 'workplace') => {
    const own = (booking?.resources ?? [])
      .filter((r) => r.kind === kind && !options.some((o) => o.value === r.resourceId))
      .map((r) => ({ value: r.resourceId, label: r.label }))
    return [...options, ...own]
  }
  const peopleOptions = withOwn(people, 'person')
  const workplaceOptions = withOwn(workplaces, 'workplace')

  if (!booking) return null

  // Any change withdraws a confirmation given for what was shown before.
  const changed = <T,>(set: (value: T) => void) => (value: T) => {
    set(value)
    setWarnings(null)
    setRefusal(null)
    setFormError(null)
  }

  const failed = (err: unknown) => {
    const shown = err instanceof ApiError ? unconfirmedWarnings(err.details) : null
    if (shown) {
      setWarnings(shown)
      return
    }
    setRefusal({ text: apiRefusalText(t, err, 'planner.booking.failed'), stale: isStaleVersion(err) })
  }

  const submit = async () => {
    const resourceIds = [...personIds, ...workplaceIds]
    if (resourceIds.length === 0) return setFormError(t('planner.booking.needsResource'))
    if (isWork && workplaceIds.length !== 1) return setFormError(t('planner.booking.workNeedsWorkplace'))
    if (!fromDate || !fromTime || !toDate || !toTime) return setFormError(t('planner.booking.timeRequired'))
    const startsAt = zurichInstant(fromDate, fromTime)
    const endsAt = zurichInstant(toDate, toTime)
    if (new Date(endsAt) <= new Date(startsAt)) return setFormError(t('planner.booking.endAfterStart'))
    setSubmitting(true)
    setRefusal(null)
    try {
      await api.patch<BookingOutcomeRead>(
        `${planningPath}/bookings/${booking.id}`,
        { startsAt, endsAt, resourceIds, confirmedWarnings: warnings ?? [] },
        { 'If-Match': String(booking.version) },
      )
      onSaved()
      onClose()
    } catch (err) {
      failed(err)
    } finally {
      setSubmitting(false)
    }
  }

  const release = async () => {
    setSubmitting(true)
    setRefusal(null)
    try {
      await api.post<BookingRead>(`${planningPath}/bookings/${booking.id}/release`, undefined, {
        'If-Match': String(booking.version),
      })
      onSaved()
      onClose()
    } catch (err) {
      setConfirmRelease(false)
      failed(err)
    } finally {
      setSubmitting(false)
    }
  }

  const facts = [
    booking.reference && `${t('planner.booking.order')}: ${booking.reference.label}`,
    booking.job && `${t('planner.booking.job')}: ${booking.job.label}`,
    booking.vehicle && `${t('planner.booking.vehicle')}: ${booking.vehicle.label}`,
  ].filter(Boolean) as string[]

  return (
    <FormDialog
      opened
      onClose={onClose}
      title={isBlock ? t('planner.booking.blockTitle') : t('planner.booking.title')}
      onSubmit={submit}
      submitLabel={warnings ? t('planner.booking.saveAnyway') : t('planner.booking.save')}
      cancelLabel={t('planner.booking.cancel')}
      submitting={submitting}
      size="lg"
    >
      <Stack gap="sm">
        <Text size="sm" fw={600}>
          {t(`planner.kinds.${booking.kind}`)}
          {facts.length > 0 ? ` · ${facts.join(' · ')}` : ''}
        </Text>
        {booking.note && <Text size="sm">{`${t('planner.fields.note')}: ${booking.note}`}</Text>}
        {booking.replan && (
          <Alert color="red" variant="light" icon={<RotateCcw size={16} />}>
            {t('planner.booking.replanSince', {
              since: zurichDateTime(booking.replan.since),
              cause: t(`planner.booking.causes.${booking.replan.cause}`),
            })}
          </Alert>
        )}
        <SimpleGrid cols={2} spacing="sm">
          <TextInput type="date" label={`${t('planner.fields.from')} – ${t('planner.fields.date')}`} value={fromDate} onChange={(e) => changed(setFromDate)(e.currentTarget.value)} required />
          <TextInput type="time" label={`${t('planner.fields.from')} – ${t('planner.fields.time')}`} value={fromTime} onChange={(e) => changed(setFromTime)(e.currentTarget.value)} required />
          <TextInput type="date" label={`${t('planner.fields.to')} – ${t('planner.fields.date')}`} value={toDate} onChange={(e) => changed(setToDate)(e.currentTarget.value)} required />
          <TextInput type="time" label={`${t('planner.fields.to')} – ${t('planner.fields.time')}`} value={toTime} onChange={(e) => changed(setToTime)(e.currentTarget.value)} required />
        </SimpleGrid>
        <MultiSelect
          label={t('planner.fields.people')}
          data={peopleOptions}
          value={personIds}
          onChange={changed(setPersonIds)}
          placeholder={personIds.length === 0 ? t('planner.fields.none') : undefined}
          searchable
        />
        {isWork ? (
          <Select
            label={t('planner.fields.workplace')}
            data={workplaceOptions}
            value={workplaceIds[0] ?? null}
            onChange={(value) => changed(setWorkplaceIds)(value ? [value] : [])}
            required
            allowDeselect={false}
          />
        ) : (
          <MultiSelect
            label={t('planner.fields.workplaces')}
            data={workplaceOptions}
            value={workplaceIds}
            onChange={changed(setWorkplaceIds)}
            placeholder={workplaceIds.length === 0 ? t('planner.fields.none') : undefined}
            searchable
          />
        )}
        {formError && (
          <Text size="sm" c="red" role="alert">
            {formError}
          </Text>
        )}
        {booking.overruledWarnings.length > 0 && (
          <List size="xs" c="dimmed">
            {booking.overruledWarnings.map((w, index) => (
              <List.Item key={index}>
                {t('planner.booking.overruled', {
                  warning: warningText(t, w, resourceLabels),
                  name: w.overruledByName,
                  at: zurichDateTime(w.overruledAt),
                })}
              </List.Item>
            ))}
          </List>
        )}
        {warnings && (
          <Alert color="yellow" variant="light" icon={<TriangleAlert size={16} />} title={t('planner.booking.warningsTitle')} role="alert">
            <Stack gap={4}>
              {warnings.map((w, index) => (
                <Text size="sm" key={index}>
                  {warningText(t, w, resourceLabels)}
                </Text>
              ))}
              <Text size="sm" fw={600}>
                {t('planner.booking.warningsNote')}
              </Text>
            </Stack>
          </Alert>
        )}
        {refusal && (
          <Alert color="red" variant="light" icon={<CircleAlert size={16} />} title={t('planner.booking.refusedTitle')} role="alert">
            <Stack gap={4}>
              <Text size="sm">{refusal.stale ? t('planner.booking.conflict') : refusal.text}</Text>
              {refusal.stale && (
                <Group>
                  <Button size="xs" variant="light" onClick={() => { onSaved(); onClose() }}>
                    {t('planner.booking.reload')}
                  </Button>
                </Group>
              )}
            </Stack>
          </Alert>
        )}
        {confirmRelease ? (
          <Alert color="red" variant="light">
            <Stack gap="xs">
              <Text size="sm">{t('planner.booking.releaseConfirm')}</Text>
              <Group gap="xs">
                <Button size="xs" color="red" onClick={release} loading={submitting}>
                  {t('planner.booking.releaseConfirmButton')}
                </Button>
                <Button size="xs" variant="default" onClick={() => setConfirmRelease(false)}>
                  {t('planner.booking.releaseBack')}
                </Button>
              </Group>
            </Stack>
          </Alert>
        ) : (
          <Group>
            <Button size="xs" variant="outline" color="red" onClick={() => setConfirmRelease(true)}>
              {t('planner.booking.release')}
            </Button>
          </Group>
        )}
      </Stack>
    </FormDialog>
  )
}
