import { useCallback, useEffect, useMemo, useState } from 'react'
import { ActionIcon, Alert, Button, Group, Select, SimpleGrid, Stack, Table, Text, TextInput, Tooltip } from '@mantine/core'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useTranslation } from 'react-i18next'
import { CalendarDays, Pencil, Plus, Trash2 } from 'lucide-react'
import { FormDialog } from '@nexotec/ui-kit'
import { api } from '../../api/client'
import type { ClosureCreate, ClosureList, ClosureRead, HolidayCalendarList, HolidayDateList } from '../../api/types'
import { useFieldHint } from '../../hooks/useFieldHint'
import { useIdempotencyKey } from '../../hooks/useIdempotencyKey'
import { toSwissLocale, type SupportedLanguage } from '../../i18n'
import { apiRefusalText } from '../../utils/apiRefusal'
import { formatDateOnly, todayLocalIsoDate } from '../../utils/format'
import { SectionCard } from '../business/SectionCard'
import { useRecordForm } from '../business/useRecordForm'
import { calendarOptions, closuresKey, holidayCalendarsKey, holidayDatesKey, holidayLabel } from './locationOptions'
import type { SectionProps } from './LocationSections'
import { useLocationPatch } from './useLocationPatch'

/**
 * The site's closed days: the cantonal holidays of the calendar it picks
 * (platform reference data, never typed by the dealer) and its own closures
 * (Betriebsferien, single days). The calendar is saved with the location
 * (If-Match); a closure is its own record (POST with an Idempotency-Key,
 * PATCH and DELETE with its own If-Match).
 */
export function HolidaysClosuresSection({ location, dealershipId, canWrite }: SectionProps) {
  const { t, i18n } = useTranslation()
  const hint = useFieldHint()
  const locale = toSwissLocale(i18n.language as SupportedLanguage)
  const year = Number(todayLocalIsoDate().slice(0, 4))
  const toForm = useCallback((l: typeof location) => ({ calendarId: l.holidayCalendarId }), [])
  const { form, set, dirty, markSaved, loaded } = useRecordForm(location, toForm)
  const patch = useLocationPatch(dealershipId, location.id)

  const calendars = useQuery({ queryKey: holidayCalendarsKey, queryFn: () => api.get<HolidayCalendarList>('/holiday-calendars') })
  const shownCalendar = form.calendarId
  const dates = useQuery({
    queryKey: holidayDatesKey(shownCalendar ?? '', year),
    queryFn: () => api.get<HolidayDateList>(`/holiday-calendars/${shownCalendar}/dates?year=${year}`),
    enabled: shownCalendar !== null,
  })
  const closures = useQuery({
    queryKey: closuresKey(dealershipId, location.id),
    queryFn: () => api.get<ClosureList>(`/dealerships/${dealershipId}/locations/${location.id}/closures`),
  })
  const [editing, setEditing] = useState<ClosureRead | 'new' | null>(null)

  const changes = form.calendarId !== loaded.holidayCalendarId ? { holidayCalendarId: form.calendarId } : {}
  return (
    <SectionCard
      title={t('locations.holidays.title')}
      icon={<CalendarDays size={20} />}
      onSave={canWrite ? () => patch.save({ changes, version: loaded.version }, { onSuccess: markSaved }) : undefined}
      dirty={dirty && Object.keys(changes).length > 0}
      saving={patch.saving}
      error={patch.error}
      onDismissError={patch.clearError}
      saved={patch.saved}
    >
      <Stack gap="md">
        <Select
          {...hint(t('locations.holidays.calendar'), t('locations.holidays.calendarHint'))}
          data={calendarOptions(calendars.data?.items ?? [], i18n.language)}
          value={form.calendarId}
          onChange={(v) => set('calendarId', v)}
          placeholder={t('locations.holidays.noCalendar')}
          searchable
          clearable
          readOnly={!canWrite}
        />
        {shownCalendar && dates.data && (
          <div>
            <Text fw={600} fz="sm" mb={4}>
              {t('locations.holidays.year', { year })}
            </Text>
            <Table withRowBorders={false} verticalSpacing={2} aria-label={t('locations.holidays.year', { year })}>
              <Table.Tbody>
                {dates.data.items.map((d) => (
                  <Table.Tr key={`${d.code}-${d.date}`}>
                    <Table.Td w={140}>{formatDateOnly(d.date, locale)}</Table.Td>
                    <Table.Td>
                      {holidayLabel(d, i18n.language)}
                      {d.afternoonOnly && (
                        <Text span c="dimmed" fz="sm">
                          {' '}
                          ({t('locations.holidays.afternoonOnly')})
                        </Text>
                      )}
                    </Table.Td>
                  </Table.Tr>
                ))}
              </Table.Tbody>
            </Table>
          </div>
        )}
        <div>
          <Group justify="space-between" mb={4}>
            <Text fw={600} fz="sm">
              {t('locations.holidays.closures')}
            </Text>
            {canWrite && (
              <Button size="xs" variant="light" leftSection={<Plus size={14} />} onClick={() => setEditing('new')}>
                {t('locations.holidays.addClosure')}
              </Button>
            )}
          </Group>
          <Text c="dimmed" fz="xs" mb={4}>
            {t('locations.holidays.closuresHint')}
          </Text>
          {closures.data && closures.data.items.length === 0 && (
            <Text c="dimmed" fz="sm" fs="italic">
              {t('locations.holidays.noClosures')}
            </Text>
          )}
          {closures.data && closures.data.items.length > 0 && (
            <Table aria-label={t('locations.holidays.closures')} verticalSpacing={4}>
              <Table.Tbody>
                {closures.data.items.map((c) => (
                  <Table.Tr key={c.id}>
                    <Table.Td w={240}>
                      {t('locations.holidays.period', { from: formatDateOnly(c.startsOn, locale), to: formatDateOnly(c.endsOn, locale) })}
                    </Table.Td>
                    <Table.Td>{c.reason}</Table.Td>
                    {canWrite && (
                      <Table.Td w={80}>
                        <Group gap={4} justify="flex-end">
                          <Tooltip label={t('locations.holidays.editClosure')}>
                            <ActionIcon variant="subtle" aria-label={t('locations.holidays.editClosure')} onClick={() => setEditing(c)}>
                              <Pencil size={14} />
                            </ActionIcon>
                          </Tooltip>
                          <RemoveClosureButton dealershipId={dealershipId} locationId={location.id} closure={c} />
                        </Group>
                      </Table.Td>
                    )}
                  </Table.Tr>
                ))}
              </Table.Tbody>
            </Table>
          )}
        </div>
      </Stack>
      <ClosureDialog dealershipId={dealershipId} locationId={location.id} editing={editing} onClose={() => setEditing(null)} />
    </SectionCard>
  )
}

function RemoveClosureButton({ dealershipId, locationId, closure }: { dealershipId: string; locationId: string; closure: ClosureRead }) {
  const { t } = useTranslation()
  const queryClient = useQueryClient()
  const [error, setError] = useState<string | null>(null)
  const remove = useMutation({
    mutationFn: () => api.delete<void>(`/dealerships/${dealershipId}/locations/${locationId}/closures/${closure.id}`, { 'If-Match': String(closure.version) }),
    onSuccess: () => void queryClient.invalidateQueries({ queryKey: closuresKey(dealershipId, locationId) }),
    onError: (err) => setError(apiRefusalText(t, err, 'locations.saveError')),
  })
  return (
    <Tooltip label={error ?? t('locations.holidays.removeClosure')} color={error ? 'red' : undefined} opened={error ? true : undefined}>
      <ActionIcon variant="subtle" color="red" aria-label={t('locations.holidays.removeClosure')} onClick={() => remove.mutate()} loading={remove.isPending}>
        <Trash2 size={14} />
      </ActionIcon>
    </Tooltip>
  )
}

function ClosureDialog({
  dealershipId,
  locationId,
  editing,
  onClose,
}: {
  dealershipId: string
  locationId: string
  editing: ClosureRead | 'new' | null
  onClose: () => void
}) {
  const { t } = useTranslation()
  const queryClient = useQueryClient()
  const idempotency = useIdempotencyKey()
  const [startsOn, setStartsOn] = useState('')
  const [endsOn, setEndsOn] = useState('')
  const [reason, setReason] = useState('')
  const [error, setError] = useState<string | null>(null)
  const existing = editing !== null && editing !== 'new' ? editing : null

  useEffect(() => {
    if (editing === null) return
    setStartsOn(existing?.startsOn ?? '')
    setEndsOn(existing?.endsOn ?? '')
    setReason(existing?.reason ?? '')
    setError(null)
    idempotency.renew()
  }, [editing, existing, idempotency])

  const body: ClosureCreate = useMemo(() => ({ startsOn, endsOn, reason: reason.trim() }), [startsOn, endsOn, reason])
  const save = useMutation({
    mutationFn: () => {
      const base = `/dealerships/${dealershipId}/locations/${locationId}/closures`
      return existing
        ? api.patch<ClosureRead>(`${base}/${existing.id}`, body, { 'If-Match': String(existing.version) })
        : api.post<ClosureRead>(base, body, idempotency.headers())
    },
    onSuccess: () => {
      idempotency.renew()
      void queryClient.invalidateQueries({ queryKey: closuresKey(dealershipId, locationId) })
      onClose()
    },
    onError: (err) => setError(apiRefusalText(t, err, 'locations.saveError')),
  })
  const complete = startsOn !== '' && endsOn !== '' && body.reason !== ''

  return (
    <FormDialog
      opened={editing !== null}
      onClose={onClose}
      title={existing ? t('locations.holidays.editClosure') : t('locations.holidays.addClosure')}
      submitLabel={t('common.save')}
      cancelLabel={t('common.cancel')}
      onSubmit={() => save.mutate()}
      submitting={save.isPending}
      submitDisabled={!complete}
    >
      <Stack gap="sm">
        <SimpleGrid cols={2}>
          <TextInput label={t('locations.holidays.startsOn')} type="date" value={startsOn} onChange={(e) => setStartsOn(e.currentTarget.value)} required />
          <TextInput label={t('locations.holidays.endsOn')} type="date" value={endsOn} onChange={(e) => setEndsOn(e.currentTarget.value)} required />
        </SimpleGrid>
        <TextInput label={t('locations.holidays.reason')} value={reason} onChange={(e) => setReason(e.currentTarget.value)} maxLength={200} required />
        {error && (
          <Alert color="red" role="alert">
            {error}
          </Alert>
        )}
      </Stack>
    </FormDialog>
  )
}
