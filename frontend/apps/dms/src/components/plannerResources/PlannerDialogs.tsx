import { useId, useState } from 'react'
import {
  Alert,
  Button,
  Checkbox,
  Group,
  Modal,
  MultiSelect,
  NumberInput,
  Select,
  Stack,
  Table,
  Text,
  TextInput,
} from '@mantine/core'
import { useTranslation } from 'react-i18next'
import { FormDialog, Picker, type PickerRow } from '@nexotec/ui-kit'
import type {
  LocationRead,
  PersonCandidateRead,
  QualificationRead,
  ResourceRead,
  TechnicianTypeRead,
  WorkingHoursUpdate,
  WorkplaceTypeRead,
} from '../../api/types'
import { useFieldHint } from '../../hooks/useFieldHint'
import { RefusalAlert } from './RefusalAlert'
import { entryName, useRefusal } from './refusal'
import { WEEKDAYS, formToWeek, weekToForm, type WorkingDayForm, type WorkingWeekForm } from './workingHours'

const LANGUAGES = ['de', 'fr', 'it', 'en'] as const

export type PlannerList = 'workplaceTypes' | 'technicianTypes' | 'qualifications'
export type ListEntry = WorkplaceTypeRead | TechnicianTypeRead | QualificationRead

/** The fields a list entry's dialog sends; on an edit only those that changed. */
export interface ListEntryBody {
  name?: string
  nameTranslations?: Record<string, string>
  rank?: number | null
  countedTimeFactor?: string | null
  isMandatory?: boolean
}

/** Runs a save, keeping its refusal on the dialog (a stale version offers a reload). */
function useSave() {
  const { refusal, refuse, clear, reloadLabel } = useRefusal()
  const [submitting, setSubmitting] = useState(false)
  const submit = async (call: () => Promise<void>) => {
    setSubmitting(true)
    clear()
    try {
      await call()
    } catch (err) {
      refuse(err)
    } finally {
      setSubmitting(false)
    }
  }
  return { refusal, reloadLabel, submitting, submit }
}

const isTechnicianType = (entry: ListEntry | null): entry is TechnicianTypeRead =>
  entry !== null && 'countedTimeFactor' in entry
const isQualification = (entry: ListEntry | null): entry is QualificationRead => entry !== null && 'isMandatory' in entry

/** A JS number from NumberInput as the decimal string the API takes (ADR-217); null when empty. */
function factorString(value: number | string): string | null {
  if (value === '') return null
  const number = typeof value === 'number' ? value : Number(value)
  return Number.isFinite(number) ? number.toFixed(2) : null
}

const sameTranslations = (a: Record<string, string>, b: Record<string, string>) =>
  JSON.stringify(Object.entries(a).sort()) === JSON.stringify(Object.entries(b).sort())

/**
 * A workplace type, a technician type or a qualification: a name with optional
 * translations (ruling 176); a technician type also its rank (1–99) and
 * counted-time factor (0.01–9.99), a qualification whether it is mandatory.
 * An edit sends only what changed (a change ends a sample, as the API does);
 * one that changed nothing sends nothing — a sample is ended by «Bestätigen».
 */
export function ListEntryDialog({
  list,
  entry,
  onClose,
  onSave,
  onReload,
}: {
  list: PlannerList
  entry: ListEntry | null
  onClose: () => void
  /** Null on an edit that changed nothing: the dialog only closes. */
  onSave: (body: ListEntryBody | null) => Promise<void>
  onReload: () => void
}) {
  const { t } = useTranslation()
  const hint = useFieldHint()
  const mandatoryHintId = useId()
  const [name, setName] = useState(entry?.name ?? '')
  const [translations, setTranslations] = useState<Record<string, string>>({ ...(entry?.nameTranslations ?? {}) })
  const technician = list === 'technicianTypes'
  const qualification = list === 'qualifications'
  const [rank, setRank] = useState<number | string>(isTechnicianType(entry) && entry.rank !== null ? entry.rank : '')
  const [factor, setFactor] = useState<number | string>(
    isTechnicianType(entry) && entry.countedTimeFactor !== null ? Number(entry.countedTimeFactor) : '',
  )
  const [mandatory, setMandatory] = useState(isQualification(entry) ? entry.isMandatory : false)
  const { refusal, reloadLabel, submitting, submit } = useSave()

  const rankValue = rank === '' ? null : Number(rank)
  const factorValue = factorString(factor)
  const rankValid = rankValue === null || (Number.isInteger(rankValue) && rankValue >= 1 && rankValue <= 99)
  const factorValid = factorValue === null || (Number(factorValue) >= 0.01 && Number(factorValue) <= 9.99)
  const given = Object.fromEntries(
    Object.entries(translations)
      .map(([language, text]) => [language, text.trim()])
      .filter(([, text]) => text !== ''),
  )

  const body = (): ListEntryBody | null => {
    const full: ListEntryBody = { name: name.trim(), nameTranslations: given }
    if (technician) Object.assign(full, { rank: rankValue, countedTimeFactor: factorValue })
    if (qualification) full.isMandatory = mandatory
    if (!entry) return full
    const changed: ListEntryBody = {}
    if (full.name !== entry.name) changed.name = full.name
    if (!sameTranslations(given, entry.nameTranslations)) changed.nameTranslations = given
    if (isTechnicianType(entry)) {
      if (rankValue !== entry.rank) changed.rank = rankValue
      const before = entry.countedTimeFactor === null ? null : Number(entry.countedTimeFactor).toFixed(2)
      if (factorValue !== before) changed.countedTimeFactor = factorValue
    }
    if (isQualification(entry) && mandatory !== entry.isMandatory) changed.isMandatory = mandatory
    return Object.keys(changed).length === 0 ? null : changed
  }

  return (
    <FormDialog
      opened
      onClose={onClose}
      title={t(`plannerResources.${list}.${entry ? 'editTitle' : 'createTitle'}`)}
      submitLabel={t('common.save')}
      cancelLabel={t('common.cancel')}
      submitting={submitting}
      submitDisabled={name.trim() === '' || !rankValid || !factorValid}
      onSubmit={() => submit(() => onSave(body()))}
    >
      <Stack gap="sm">
        <RefusalAlert refusal={refusal} reloadLabel={reloadLabel} onReload={onReload} />
        <TextInput
          {...hint(t('plannerResources.entryDialog.name'), t('plannerResources.entryDialog.nameHint'))}
          required
          maxLength={100}
          value={name}
          onChange={(event) => setName(event.currentTarget.value)}
          data-autofocus
        />
        {LANGUAGES.map((language) => (
          <TextInput
            key={language}
            label={t('plannerResources.entryDialog.translation', {
              language: t(`plannerResources.languages.${language}`),
            })}
            maxLength={100}
            value={translations[language] ?? ''}
            onChange={(event) => {
              const text = event.currentTarget.value
              setTranslations((current) => ({ ...current, [language]: text }))
            }}
          />
        ))}
        {technician && (
          <>
            <NumberInput
              {...hint(t('plannerResources.entryDialog.rank'), t('plannerResources.entryDialog.rankHint'))}
              min={1}
              max={99}
              allowDecimal={false}
              allowNegative={false}
              value={rank}
              onChange={setRank}
              error={rankValid ? undefined : t('plannerResources.entryDialog.rankInvalid')}
            />
            <NumberInput
              {...hint(t('plannerResources.entryDialog.factor'), t('plannerResources.entryDialog.factorHint'))}
              min={0.01}
              max={9.99}
              step={0.05}
              decimalScale={2}
              allowNegative={false}
              value={factor}
              onChange={setFactor}
              error={factorValid ? undefined : t('plannerResources.entryDialog.factorInvalid')}
            />
          </>
        )}
        {qualification && (
          <Checkbox
            {...hint(t('plannerResources.entryDialog.mandatory'), t('plannerResources.entryDialog.mandatoryHint'), {
              descriptionId: mandatoryHintId,
            })}
            checked={mandatory}
            onChange={(event) => setMandatory(event.currentTarget.checked)}
          />
        )}
      </Stack>
    </FormDialog>
  )
}

/** Confirms a destructive step — deleting a list entry, deactivating a resource. */
export function ConfirmDialog({
  title,
  body,
  actionLabel,
  onClose,
  onConfirm,
  onReload,
}: {
  title: string
  body: string
  actionLabel: string
  onClose: () => void
  onConfirm: () => Promise<void>
  onReload: () => void
}) {
  const { t } = useTranslation()
  const { refusal, reloadLabel, submitting, submit } = useSave()
  return (
    <FormDialog
      opened
      destructive
      onClose={onClose}
      title={title}
      submitLabel={actionLabel}
      cancelLabel={t('common.cancel')}
      submitting={submitting}
      onSubmit={() => submit(onConfirm)}
    >
      <Stack gap="sm">
        <RefusalAlert refusal={refusal} reloadLabel={reloadLabel} onReload={onReload} />
        <Text fz="sm">{body}</Text>
      </Stack>
    </FormDialog>
  )
}

export interface ResourceBody {
  name?: string
  userId?: string
  locationId?: string
  workplaceTypeId?: string | null
  technicianTypeId?: string | null
  qualificationIds?: string[]
  sortOrder?: number
}

const sameIds = (a: readonly string[], b: readonly string[]) => [...a].sort().join() === [...b].sort().join()

/**
 * A person (a user who works in the Betrieb, picked from the candidates with
 * the kit's Picker) or a workplace (a name), at one location, with its type —
 * and for a person the qualifications. On an edit only what changed is sent;
 * moving a resource to another location says what happens to its bookings.
 */
export function ResourceDialog({
  kind,
  resource,
  candidates,
  locations,
  workplaceTypes,
  technicianTypes,
  qualifications,
  onClose,
  onSave,
  onReload,
}: {
  kind: 'person' | 'workplace'
  resource: ResourceRead | null
  /** Who may become a person resource (create only). */
  candidates: PersonCandidateRead[]
  /** The Betrieb's active locations. */
  locations: LocationRead[]
  workplaceTypes: WorkplaceTypeRead[]
  technicianTypes: TechnicianTypeRead[]
  qualifications: QualificationRead[]
  onClose: () => void
  /** Null on an edit that changed nothing. */
  onSave: (body: ResourceBody | null) => Promise<void>
  onReload: () => void
}) {
  const { t, i18n } = useTranslation()
  const hint = useFieldHint()
  const language = i18n.language
  const person = kind === 'person'
  const [user, setUser] = useState<{ id: string; label: string } | null>(
    resource?.userId ? { id: resource.userId, label: resource.userLabel ?? '' } : null,
  )
  const [pickerOpen, setPickerOpen] = useState(false)
  const [query, setQuery] = useState('')
  const [name, setName] = useState(resource?.name ?? '')
  const [locationId, setLocationId] = useState<string | null>(
    resource?.locationId ?? (locations.length === 1 ? locations[0].id : null),
  )
  const [typeId, setTypeId] = useState<string | null>(
    (person ? resource?.technicianTypeId : resource?.workplaceTypeId) ?? null,
  )
  const [qualificationIds, setQualificationIds] = useState<string[]>(resource?.qualificationIds ?? [])
  const [sortOrder, setSortOrder] = useState<number | string>(resource?.sortOrder ?? 0)
  const { refusal, reloadLabel, submitting, submit } = useSave()

  // A deactivated location the resource still stands at stays selectable as it is.
  const locationOptions = locations.map((location) => ({ value: location.id, label: location.name }))
  if (resource && !locations.some((location) => location.id === resource.locationId)) {
    locationOptions.push({ value: resource.locationId, label: resource.locationLabel })
  }
  const types = person ? technicianTypes : workplaceTypes
  const sortValue = sortOrder === '' ? 0 : Number(sortOrder)
  const sortValid = Number.isInteger(sortValue) && sortValue >= 0 && sortValue <= 9999
  const ready =
    locationId !== null && sortValid && (person ? user !== null : name.trim() !== '')
  const moving = resource !== null && locationId !== null && locationId !== resource.locationId
  const rows: PickerRow[] = candidates
    .filter((candidate) => candidate.displayName.toLocaleLowerCase().includes(query.trim().toLocaleLowerCase()))
    .map((candidate) => ({ id: candidate.userId, label: candidate.displayName }))

  const body = (): ResourceBody | null => {
    const full: ResourceBody = { locationId: locationId ?? '', sortOrder: sortValue }
    if (person) {
      full.technicianTypeId = typeId
      full.qualificationIds = qualificationIds
    } else {
      full.name = name.trim()
      full.workplaceTypeId = typeId
    }
    if (!resource) return person ? { ...full, userId: user?.id ?? '' } : full
    const changed: ResourceBody = {}
    if (full.locationId !== resource.locationId) changed.locationId = full.locationId
    if (full.sortOrder !== resource.sortOrder) changed.sortOrder = full.sortOrder
    if (person) {
      if (typeId !== resource.technicianTypeId) changed.technicianTypeId = typeId
      if (!sameIds(qualificationIds, resource.qualificationIds)) changed.qualificationIds = qualificationIds
    } else {
      if (full.name !== resource.name) changed.name = full.name
      if (typeId !== resource.workplaceTypeId) changed.workplaceTypeId = typeId
    }
    return Object.keys(changed).length === 0 ? null : changed
  }

  const titleKey = `plannerResources.resourceDialog.${person ? 'person' : 'workplace'}${resource ? 'EditTitle' : 'CreateTitle'}`
  return (
    <>
      <FormDialog
        opened={!pickerOpen}
        onClose={onClose}
        title={t(titleKey)}
        submitLabel={t('common.save')}
        cancelLabel={t('common.cancel')}
        submitting={submitting}
        submitDisabled={!ready}
        onSubmit={() => submit(() => onSave(body()))}
      >
        <Stack gap="sm">
          <RefusalAlert refusal={refusal} reloadLabel={reloadLabel} onReload={onReload} />
          {person ? (
            <div>
              <Text fz="sm" fw={500}>
                {t('plannerResources.resourceDialog.person')}
              </Text>
              <Group gap="xs">
                <Text fz="sm">{user?.label ?? t('plannerResources.resourceDialog.noPerson')}</Text>
                {!resource && (
                  <Button size="compact-xs" variant="default" onClick={() => setPickerOpen(true)}>
                    {t('plannerResources.resourceDialog.choosePerson')}
                  </Button>
                )}
              </Group>
            </div>
          ) : (
            <TextInput
              label={t('plannerResources.resourceDialog.workplaceName')}
              required
              maxLength={100}
              value={name}
              onChange={(event) => setName(event.currentTarget.value)}
              data-autofocus
            />
          )}
          <Select
            label={t('plannerResources.columns.location')}
            required
            data={locationOptions}
            value={locationId}
            onChange={setLocationId}
          />
          {moving && (
            <Alert color="yellow" variant="light">
              {t('plannerResources.resourceDialog.movingNote')}
            </Alert>
          )}
          <Select
            label={t(person ? 'plannerResources.columns.technicianType' : 'plannerResources.columns.workplaceType')}
            clearable
            data={types.map((type) => ({ value: type.id, label: entryName(type, language) }))}
            value={typeId}
            onChange={setTypeId}
          />
          {person && (
            <MultiSelect
              label={t('plannerResources.columns.qualifications')}
              data={qualifications.map((entry) => ({ value: entry.id, label: entryName(entry, language) }))}
              value={qualificationIds}
              onChange={setQualificationIds}
              clearable
            />
          )}
          <NumberInput
            {...hint(t('plannerResources.resourceDialog.sortOrder'), t('plannerResources.resourceDialog.sortOrderHint'))}
            min={0}
            max={9999}
            allowDecimal={false}
            allowNegative={false}
            value={sortOrder}
            onChange={setSortOrder}
          />
        </Stack>
      </FormDialog>
      <Modal opened={pickerOpen} onClose={() => setPickerOpen(false)} title={t('plannerResources.resourceDialog.choosePerson')}>
        <Picker
          rows={rows}
          query={query}
          onQueryChange={setQuery}
          onSelect={(row) => {
            setUser({ id: row.id, label: row.label })
            setPickerOpen(false)
          }}
          placeholder={t('plannerResources.resourceDialog.searchPerson')}
          inputLabel={t('plannerResources.resourceDialog.searchPerson')}
          emptyLabel={t(candidates.length === 0 ? 'plannerResources.resourceDialog.noCandidates' : 'common.noMatches')}
        />
      </Modal>
    </>
  )
}

/**
 * A person's working week: a row per weekday, an optional second interval
 * (after the lunch break). Sent whole as a PUT under the resource's If-Match.
 * A reader sees the same week without inputs.
 */
export function WorkingHoursDialog({
  resource,
  canWrite,
  onClose,
  onSave,
  onReload,
}: {
  resource: ResourceRead
  canWrite: boolean
  onClose: () => void
  onSave: (body: WorkingHoursUpdate) => Promise<void>
  onReload: () => void
}) {
  const { t } = useTranslation()
  const [week, setWeek] = useState<WorkingWeekForm>(() => weekToForm(resource.workingHours))
  const [invalid, setInvalid] = useState<string | null>(null)
  const { refusal, reloadLabel, submitting, submit } = useSave()
  const title = t('plannerResources.hours.title', { name: resource.userLabel ?? '' })
  const setDay = (weekday: number, patch: Partial<WorkingDayForm>) => {
    setInvalid(null)
    setWeek((current) => ({ ...current, [weekday]: { ...current[weekday], ...patch } }))
  }

  const table = (
    <Table withRowBorders={false} verticalSpacing={4} aria-label={title}>
      <Table.Thead>
        <Table.Tr>
          <Table.Th />
          <Table.Th>{t('plannerResources.hours.startsAt')}</Table.Th>
          <Table.Th>{t('plannerResources.hours.endsAt')}</Table.Th>
          <Table.Th>{t('plannerResources.hours.secondStartsAt')}</Table.Th>
          <Table.Th>{t('plannerResources.hours.secondEndsAt')}</Table.Th>
        </Table.Tr>
      </Table.Thead>
      <Table.Tbody>
        {WEEKDAYS.map((weekday) => {
          const day = week[weekday]
          const dayName = t(`locations.hours.weekdays.${weekday}`)
          const time = (field: 'startsAt' | 'endsAt' | 'secondStartsAt' | 'secondEndsAt', label: string) => (
            <Table.Td>
              {canWrite ? (
                <TextInput
                  type="time"
                  size="xs"
                  aria-label={`${dayName} ${label}`}
                  value={day[field]}
                  onChange={(event) => setDay(weekday, { [field]: event.currentTarget.value })}
                />
              ) : (
                <Text fz="sm">{day[field] || '–'}</Text>
              )}
            </Table.Td>
          )
          return (
            <Table.Tr key={weekday}>
              <Table.Td w={140}>
                {canWrite ? (
                  <Checkbox
                    label={dayName}
                    checked={day.works}
                    onChange={(event) =>
                      setDay(
                        weekday,
                        event.currentTarget.checked
                          ? { works: true, startsAt: day.startsAt || '07:30', endsAt: day.endsAt || '17:00' }
                          : { works: false },
                      )
                    }
                  />
                ) : (
                  <Text fz="sm">{dayName}</Text>
                )}
              </Table.Td>
              {day.works ? (
                <>
                  {time('startsAt', t('plannerResources.hours.startsAt'))}
                  {time('endsAt', t('plannerResources.hours.endsAt'))}
                  {time('secondStartsAt', t('plannerResources.hours.secondStartsAt'))}
                  {time('secondEndsAt', t('plannerResources.hours.secondEndsAt'))}
                </>
              ) : (
                <Table.Td colSpan={4}>
                  <Text c="dimmed" fz="sm">
                    {t('plannerResources.hours.off')}
                  </Text>
                </Table.Td>
              )}
            </Table.Tr>
          )
        })}
      </Table.Tbody>
    </Table>
  )

  if (!canWrite) {
    return (
      <Modal opened onClose={onClose} title={title} size="lg">
        {resource.workingHours.length === 0 ? (
          <Text c="dimmed" fz="sm">
            {t('plannerResources.hours.none')}
          </Text>
        ) : (
          table
        )}
      </Modal>
    )
  }
  return (
    <FormDialog
      opened
      size="lg"
      onClose={onClose}
      title={title}
      submitLabel={t('common.save')}
      cancelLabel={t('common.cancel')}
      submitting={submitting}
      onSubmit={() => {
        const result = formToWeek(week)
        if ('invalidWeekday' in result) {
          setInvalid(t('plannerResources.hours.invalid', { day: t(`locations.hours.weekdays.${result.invalidWeekday}`) }))
          return
        }
        return submit(() => onSave(result.body))
      }}
    >
      <Stack gap="sm">
        <RefusalAlert refusal={refusal} reloadLabel={reloadLabel} onReload={onReload} />
        {invalid && (
          <Alert color="red" role="alert">
            {invalid}
          </Alert>
        )}
        {table}
      </Stack>
    </FormDialog>
  )
}
