import { useEffect, useState } from 'react'
import { Alert, MultiSelect, Select, SimpleGrid, Stack, Text, TextInput } from '@mantine/core'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useTranslation } from 'react-i18next'
import { FormDialog } from '@nexotec/ui-kit'
import { api } from '../../api/client'
import type { HolidayCalendarList, LocationCreate, LocationRead, LocationType } from '../../api/types'
import { CANTON_OPTIONS } from '../../customerOptions'
import { useFieldHint } from '../../hooks/useFieldHint'
import { useIdempotencyKey } from '../../hooks/useIdempotencyKey'
import { apiRefusalText } from '../../utils/apiRefusal'
import { calendarOptions, holidayCalendarsKey, locationTypeOptions, locationsKey } from './locationOptions'

interface CreateForm {
  code: string
  name: string
  locationTypes: LocationType[]
  street: string
  houseNumber: string
  postalCode: string
  locality: string
  canton: string | null
  holidayCalendarId: string | null
}

const EMPTY: CreateForm = {
  code: '',
  name: '',
  locationTypes: [],
  street: '',
  houseNumber: '',
  postalCode: '',
  locality: '',
  canton: null,
  holidayCalendarId: null,
}

/**
 * KAN-204 — a new location: the code (fixed once saved), the name, what the
 * site is, its Swiss address and its holiday calendar — the canton's own one
 * is taken when none is picked and the canton has one. Hours, contacts and
 * closures are set on the location's own screen, which opens after the save.
 */
export function LocationCreateDialog({
  opened,
  onClose,
  dealershipId,
  onCreated,
}: {
  opened: boolean
  onClose: () => void
  dealershipId: string
  onCreated: (location: LocationRead) => void
}) {
  const { t, i18n } = useTranslation()
  const hint = useFieldHint()
  const queryClient = useQueryClient()
  const idempotency = useIdempotencyKey()
  const [form, setForm] = useState<CreateForm>(EMPTY)
  const [error, setError] = useState<string | null>(null)
  const set = <K extends keyof CreateForm>(key: K, value: CreateForm[K]) => setForm((f) => ({ ...f, [key]: value }))

  useEffect(() => {
    if (opened) {
      setForm(EMPTY)
      setError(null)
      idempotency.renew()
    }
  }, [opened, idempotency])

  const calendars = useQuery({
    queryKey: holidayCalendarsKey,
    queryFn: () => api.get<HolidayCalendarList>('/holiday-calendars'),
    enabled: opened,
  })
  const cantonCalendars = (calendars.data?.items ?? []).filter((c) => c.canton === form.canton)

  const create = useMutation({
    mutationFn: (body: LocationCreate) => api.post<LocationRead>(`/dealerships/${dealershipId}/locations`, body, idempotency.headers()),
    onSuccess: (created) => {
      idempotency.renew()
      void queryClient.invalidateQueries({ queryKey: locationsKey(dealershipId), exact: true })
      onCreated(created)
    },
    onError: (err) => setError(apiRefusalText(t, err, 'locations.saveError')),
  })

  const body: LocationCreate = {
    code: form.code.trim(),
    name: form.name.trim(),
    locationTypes: form.locationTypes,
    addressStreet: form.street.trim(),
    addressHouseNumber: form.houseNumber.trim(),
    addressPostalCode: form.postalCode.trim(),
    addressLocality: form.locality.trim(),
    addressCanton: form.canton,
    addressCountry: 'CH',
    ...(form.holidayCalendarId ? { holidayCalendarId: form.holidayCalendarId } : {}),
  }
  const complete = Boolean(body.code && body.name && body.addressStreet && body.addressHouseNumber && body.addressPostalCode && body.addressLocality && body.addressCanton)

  return (
    <FormDialog
      opened={opened}
      onClose={onClose}
      title={t('locations.create.title')}
      submitLabel={t('locations.create.submit')}
      cancelLabel={t('common.cancel')}
      onSubmit={() => create.mutate(body)}
      submitting={create.isPending}
      submitDisabled={!complete}
      size="lg"
    >
      <Stack gap="sm">
        <SimpleGrid cols={{ base: 1, sm: 2 }}>
          <TextInput {...hint(t('locations.master.code'), t('locations.master.codeHint'))} value={form.code} onChange={(e) => set('code', e.currentTarget.value.toUpperCase())} maxLength={20} required />
          <TextInput label={t('locations.master.name')} value={form.name} onChange={(e) => set('name', e.currentTarget.value)} maxLength={200} required />
        </SimpleGrid>
        <MultiSelect {...hint(t('locations.master.types'), t('locations.master.typesHint'))} data={locationTypeOptions(t)} value={form.locationTypes} onChange={(v) => set('locationTypes', v as LocationType[])} />
        <SimpleGrid cols={{ base: 1, sm: 3 }}>
          <TextInput label={t('business.addresses.street')} value={form.street} onChange={(e) => set('street', e.currentTarget.value)} required />
          <TextInput label={t('business.addresses.houseNumber')} value={form.houseNumber} onChange={(e) => set('houseNumber', e.currentTarget.value)} required />
          <TextInput label={t('business.addresses.postalCode')} value={form.postalCode} onChange={(e) => set('postalCode', e.currentTarget.value)} required />
          <TextInput label={t('business.addresses.locality')} value={form.locality} onChange={(e) => set('locality', e.currentTarget.value)} required />
          <Select
            label={t('business.addresses.canton')}
            data={CANTON_OPTIONS}
            value={form.canton}
            onChange={(v) => setForm((f) => ({ ...f, canton: v, holidayCalendarId: null }))}
            searchable
            required
          />
        </SimpleGrid>
        {cantonCalendars.length > 1 && (
          <Select
            {...hint(t('locations.holidays.calendar'), t('locations.holidays.calendarHint'))}
            data={calendarOptions(cantonCalendars, i18n.language)}
            value={form.holidayCalendarId}
            onChange={(v) => set('holidayCalendarId', v)}
            clearable
          />
        )}
        {error && (
          <Alert color="red" role="alert">
            {error}
          </Alert>
        )}
      </Stack>
    </FormDialog>
  )
}

/** ADR-061's deactivation, confirmed: never a delete — documents that name the location keep it. */
export function DeactivateLocationDialog({
  location,
  onCancel,
  onConfirm,
  saving,
  error,
}: {
  location: LocationRead | null
  onCancel: () => void
  onConfirm: () => void
  saving: boolean
  error: string | null
}) {
  const { t } = useTranslation()
  return (
    <FormDialog
      opened={location !== null}
      onClose={onCancel}
      title={t('locations.actions.deactivate')}
      submitLabel={t('locations.actions.deactivate')}
      cancelLabel={t('common.cancel')}
      onSubmit={onConfirm}
      submitting={saving}
      destructive
    >
      <Stack gap="sm">
        <Text>{t('locations.actions.deactivateConfirm', { name: location?.name ?? '' })}</Text>
        {error && (
          <Alert color="red" role="alert">
            {error}
          </Alert>
        )}
      </Stack>
    </FormDialog>
  )
}
