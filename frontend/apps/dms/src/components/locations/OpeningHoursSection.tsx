import { useEffect, useId, useState } from 'react'
import { Checkbox, SegmentedControl, Stack, Switch, Table, Text, TextInput } from '@mantine/core'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useTranslation } from 'react-i18next'
import { Clock } from 'lucide-react'
import { api } from '../../api/client'
import type { LocationDepartment, OpeningHoursRead, OpeningHoursUpdate } from '../../api/types'
import { useFieldHint } from '../../hooks/useFieldHint'
import { apiRefusalText } from '../../utils/apiRefusal'
import { SectionCard } from '../business/SectionCard'
import { DEPARTMENTS, WEEKDAYS, locationKey, locationsKey, openingHoursKey } from './locationOptions'
import { formToApi, hoursToForm, withPerDepartment, type DayForm, type HoursForm } from './openingHours'
import type { SectionProps } from './LocationSections'

/**
 * Opening hours (D-A-27): one set for the whole site by default; the toggle
 * "differs per department" opens a week each for Sales, Service and Parts.
 * Either way the API stores a row per department — the one-set mode writes
 * the same week three times. The PUT replaces the week under the location's
 * If-Match and moves its version, so the location is fetched again after.
 */
export function OpeningHoursSection({ location, dealershipId, canWrite }: SectionProps) {
  const { t } = useTranslation()
  const hint = useFieldHint()
  const switchHintId = useId()
  const queryClient = useQueryClient()
  const hours = useQuery({
    queryKey: openingHoursKey(dealershipId, location.id),
    queryFn: () => api.get<OpeningHoursRead>(`/dealerships/${dealershipId}/locations/${location.id}/opening-hours`),
  })
  const [form, setForm] = useState<HoursForm>(() => hoursToForm(undefined))
  const [dirty, setDirty] = useState(false)
  const [department, setDepartment] = useState<LocationDepartment>('sales')
  const [error, setError] = useState<string | null>(null)
  const [saved, setSaved] = useState(false)

  useEffect(() => {
    if (hours.data && !dirty) setForm(hoursToForm(hours.data))
  }, [hours.data, dirty])

  const save = useMutation({
    mutationFn: (body: OpeningHoursUpdate) =>
      api.put<OpeningHoursRead>(`/dealerships/${dealershipId}/locations/${location.id}/opening-hours`, body, { 'If-Match': String(location.version) }),
    onSuccess: (read) => {
      setError(null)
      setSaved(true)
      setDirty(false)
      queryClient.setQueryData(openingHoursKey(dealershipId, location.id), read)
      setForm(hoursToForm(read))
      // The PUT moved the location's version: every section needs the new one.
      void queryClient.invalidateQueries({ queryKey: locationKey(dealershipId, location.id), exact: true })
      void queryClient.invalidateQueries({ queryKey: locationsKey(dealershipId), exact: true })
    },
    onError: (err) => {
      setSaved(false)
      setError(apiRefusalText(t, err, 'locations.saveError'))
    },
  })

  const update = (next: HoursForm) => {
    setForm(next)
    setDirty(true)
    setSaved(false)
  }
  const shown: LocationDepartment = form.perDepartment ? department : 'sales'
  const setDay = (weekday: number, patch: Partial<DayForm>) =>
    update({ ...form, weeks: { ...form.weeks, [shown]: { ...form.weeks[shown], [weekday]: { ...form.weeks[shown][weekday], ...patch } } } })

  const onSave = () => {
    const result = formToApi(form)
    if ('invalid' in result) {
      setError(t('locations.hours.invalid', { day: t(`locations.hours.weekdays.${result.invalid.weekday}`) }))
      if (result.invalid.department) setDepartment(result.invalid.department)
      return
    }
    save.mutate(result.body)
  }

  const week = form.weeks[shown]
  return (
    <SectionCard
      title={t('locations.hours.title')}
      icon={<Clock size={20} />}
      onSave={canWrite ? onSave : undefined}
      dirty={dirty}
      saving={save.isPending}
      error={error}
      onDismissError={() => setError(null)}
      saved={saved}
    >
      <Stack gap="sm">
        <Switch
          {...hint(t('locations.hours.perDepartment'), t('locations.hours.perDepartmentHint'), { descriptionId: switchHintId })}
          checked={form.perDepartment}
          onChange={(e) => update(withPerDepartment(form, e.currentTarget.checked))}
          disabled={!canWrite}
        />
        {form.perDepartment && (
          <SegmentedControl
            value={department}
            onChange={(v) => setDepartment(v as LocationDepartment)}
            data={DEPARTMENTS.map((d) => ({ value: d, label: t(`locations.hours.departments.${d}`) }))}
            aria-label={t('locations.hours.perDepartment')}
          />
        )}
        {hours.isSuccess && hours.data.hours.length === 0 && !dirty && (
          <Text c="dimmed" fz="sm" fs="italic">
            {t('locations.hours.none')}
          </Text>
        )}
        <Table withRowBorders={false} verticalSpacing={4} aria-label={t('locations.hours.title')}>
          <Table.Tbody>
            {WEEKDAYS.map((weekday) => {
              const day = week[weekday]
              const dayName = t(`locations.hours.weekdays.${weekday}`)
              return (
                <Table.Tr key={weekday}>
                  <Table.Td w={140}>
                    <Checkbox
                      label={dayName}
                      checked={day.open}
                      onChange={(e) => setDay(weekday, e.currentTarget.checked ? { open: true, opensAt: day.opensAt || '08:00', closesAt: day.closesAt || '18:00' } : { open: false })}
                      disabled={!canWrite}
                    />
                  </Table.Td>
                  {day.open ? (
                    <>
                      <Table.Td>
                        <TextInput type="time" aria-label={`${dayName} ${t('locations.hours.opensAt')}`} value={day.opensAt} onChange={(e) => setDay(weekday, { opensAt: e.currentTarget.value })} readOnly={!canWrite} size="xs" />
                      </Table.Td>
                      <Table.Td>
                        <TextInput type="time" aria-label={`${dayName} ${t('locations.hours.closesAt')}`} value={day.closesAt} onChange={(e) => setDay(weekday, { closesAt: e.currentTarget.value })} readOnly={!canWrite} size="xs" />
                      </Table.Td>
                      <Table.Td>
                        <TextInput type="time" aria-label={`${dayName} ${t('locations.hours.secondOpensAt')}`} value={day.secondOpensAt} onChange={(e) => setDay(weekday, { secondOpensAt: e.currentTarget.value })} readOnly={!canWrite} size="xs" />
                      </Table.Td>
                      <Table.Td>
                        <TextInput type="time" aria-label={`${dayName} ${t('locations.hours.secondClosesAt')}`} value={day.secondClosesAt} onChange={(e) => setDay(weekday, { secondClosesAt: e.currentTarget.value })} readOnly={!canWrite} size="xs" />
                      </Table.Td>
                    </>
                  ) : (
                    <Table.Td colSpan={4}>
                      <Text c="dimmed" fz="sm">
                        {t('locations.hours.closed')}
                      </Text>
                    </Table.Td>
                  )}
                </Table.Tr>
              )
            })}
          </Table.Tbody>
        </Table>
      </Stack>
    </SectionCard>
  )
}
