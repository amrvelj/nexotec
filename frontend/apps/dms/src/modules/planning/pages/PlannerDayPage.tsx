import { useMemo, useState } from 'react'
import { Link, useSearchParams } from 'react-router-dom'
import { ActionIcon, Alert, Button, Group, Select, Skeleton, Stack, Text, Title, Tooltip } from '@mantine/core'
import { Ban, CalendarCog, ChevronLeft, ChevronRight, CircleAlert, Info, SlidersHorizontal } from 'lucide-react'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import { useTranslation } from 'react-i18next'
import { ResourceDayGrid, useSetBreadcrumb, type DayGridGroup, type DayGridItem, type DayGridSpan } from '@nexotec/ui-kit'
import { api } from '../../../api/client'
import type {
  BookingRead,
  LocationList,
  PlannerDayRead,
  ResourceList,
  ResourceRead,
  TechnicianTypeList,
  WorkplaceTypeList,
} from '../../../api/types'
import { useAuth } from '../../../auth/AuthContext'
import { formatLongDateOnly } from '../../../utils/format'
import { useCanWrite } from '../../../auth/useCanWrite'
import { BlockResourceDialog } from '../components/BlockResourceDialog'
import { BookingDialog, type PlannerOption } from '../components/BookingDialog'
import { clockOf, isoWeekday, minuteOfDay, minutesOf, shiftDay, wallClock, zurichToday } from '../plannerTime'

const ISO_DAY = /^\d{4}-\d{2}-\d{2}$/
const DAY_MINUTES = 24 * 60

/** A list entry's name in the UI language (ruling 176): its translation, else its name. */
function entryName(entry: { name: string; nameTranslations: Record<string, string> } | undefined, language: string): string | undefined {
  if (!entry) return undefined
  const translated = entry.nameTranslations[language.slice(0, 2)]
  return translated && translated.trim() !== '' ? translated : entry.name
}

/** The hatched spans of a person on a weekday: outside the hours entered. A person with no hours
 * entered at all is shown without any (the server warns for none either, planning.md). */
function offDuty(resource: ResourceRead, weekday: number): DayGridSpan[] {
  if (resource.kind !== 'person' || resource.workingHours.length === 0) return []
  const day = resource.workingHours.find((d) => d.weekday === weekday)
  if (!day) return [{ startMinute: 0, endMinute: DAY_MINUTES }]
  const spans: DayGridSpan[] = [{ startMinute: 0, endMinute: minutesOf(day.startsAt) }]
  if (day.secondStartsAt && day.secondEndsAt) {
    spans.push({ startMinute: minutesOf(day.endsAt), endMinute: minutesOf(day.secondStartsAt) })
    spans.push({ startMinute: minutesOf(day.secondEndsAt), endMinute: DAY_MINUTES })
  } else {
    spans.push({ startMinute: minutesOf(day.endsAt), endMinute: DAY_MINUTES })
  }
  return spans.filter((s) => s.endMinute > s.startMinute)
}

/**
 * KAN-809 — PL1, the planner: one day of one location, a row per person and
 * per workplace, the grid of setting O6, the bookings and blocks. A booking
 * opens the booking dialog (PL2: change, release); «Ressource blockieren»
 * opens PL4. New work and slots are booked from the order (KAN-812; Anto,
 * 2026-11-26), so there is no «Neue Buchung» here. Behind the Aftersales
 * module (nav and API); the day and the location are in the URL
 * (FR-UI-06). Every time is the Betrieb's — Europe/Zurich — whatever the
 * browser's zone.
 */
export function PlannerDayPage() {
  const { t, i18n } = useTranslation()
  useSetBreadcrumb([t('planner.title')])
  const { activeDealership } = useAuth()
  const dealershipId = activeDealership?.id ?? ''
  const canWrite = useCanWrite('planner')
  const queryClient = useQueryClient()
  const [params, setParams] = useSearchParams()
  const [opened, setOpened] = useState<BookingRead | null>(null)
  const [blocking, setBlocking] = useState(false)
  const [marked, setMarked] = useState<number | null>(null)
  const base = `/dealerships/${dealershipId}`
  const planningPath = `${base}/planning`
  const enabled = dealershipId !== ''
  const language = i18n.language

  const requestedDay = params.get('date')
  const day = requestedDay && ISO_DAY.test(requestedDay) ? requestedDay : zurichToday()

  const locations = useQuery({
    queryKey: ['locations', dealershipId],
    queryFn: () => api.get<LocationList>(`${base}/locations`),
    enabled,
  })
  const activeLocations = useMemo(() => (locations.data?.items ?? []).filter((l) => l.isActive), [locations.data])
  const requestedLocation = params.get('location')
  const locationId =
    activeLocations.find((l) => l.id === requestedLocation)?.id ??
    (requestedLocation && locations.data?.items.some((l) => l.id === requestedLocation) ? requestedLocation : activeLocations[0]?.id)
  const location = locations.data?.items.find((l) => l.id === locationId)

  const resources = useQuery({
    queryKey: ['planner', dealershipId, 'resources', locationId],
    queryFn: () => api.get<ResourceList>(`${planningPath}/resources?locationId=${locationId}&includeInactive=true`),
    enabled: enabled && !!locationId,
  })
  const technicianTypes = useQuery({
    queryKey: ['planner', dealershipId, 'technician-types'],
    queryFn: () => api.get<TechnicianTypeList>(`${planningPath}/technician-types`),
    enabled,
  })
  const workplaceTypes = useQuery({
    queryKey: ['planner', dealershipId, 'workplace-types'],
    queryFn: () => api.get<WorkplaceTypeList>(`${planningPath}/workplace-types`),
    enabled,
  })
  const plannerDay = useQuery({
    queryKey: ['planner', dealershipId, 'day', locationId, day],
    queryFn: () => api.get<PlannerDayRead>(`${planningPath}/day?locationId=${locationId}&date=${day}`),
    enabled: enabled && !!locationId,
  })
  const refresh = () => queryClient.invalidateQueries({ queryKey: ['planner', dealershipId] })

  const go = (next: { date?: string; location?: string }) => {
    const search = new URLSearchParams(params)
    if (next.date) search.set('date', next.date)
    if (next.location) search.set('location', next.location)
    setParams(search)
    setMarked(null)
  }

  // A deactivated resource gets a row only while it holds a booking of the day: its bookings are
  // marked «neu einplanen» (planning.md) and this page is where they are moved or released.
  const held = new Set((plannerDay.data?.bookings ?? []).flatMap((b) => b.resources.map((r) => r.resourceId)))
  const shown = (resources.data?.items ?? []).filter((r) => r.isActive || held.has(r.id))
  const people = shown.filter((r) => r.kind === 'person')
  const workplaces = shown.filter((r) => r.kind === 'workplace')
  // Only an active resource is offered for a booking; the server refuses an inactive one.
  const personOptions: PlannerOption[] = people.filter((r) => r.isActive).map((r) => ({ value: r.id, label: r.userLabel ?? '' }))
  const workplaceOptions: PlannerOption[] = workplaces.filter((r) => r.isActive).map((r) => ({ value: r.id, label: r.name ?? '' }))
  const resourceLabels = useMemo(() => {
    const labels = new Map<string, string>()
    for (const r of resources.data?.items ?? []) labels.set(r.id, r.name ?? r.userLabel ?? '')
    for (const b of plannerDay.data?.bookings ?? []) for (const r of b.resources) if (!labels.has(r.resourceId)) labels.set(r.resourceId, r.label)
    return labels
  }, [resources.data, plannerDay.data])

  const weekday = isoWeekday(day)
  const bookings = plannerDay.data?.bookings ?? []
  const itemOf = (booking: BookingRead, row: string): DayGridItem => {
    const startMinute = Math.max(0, minuteOfDay(booking.startsAt, day))
    const endMinute = Math.min(DAY_MINUTES, minuteOfDay(booking.endsAt, day))
    const from = wallClock(booking.startsAt).time
    const to = wallClock(booking.endsAt).time
    const kind = t(`planner.kinds.${booking.kind}`)
    const title =
      booking.kind === 'block'
        ? [kind, booking.note].filter(Boolean).join(' · ')
        : booking.kind === 'work'
          ? [booking.reference?.label, booking.job?.label].filter(Boolean).join(' · ')
          : [kind, booking.reference?.label].filter(Boolean).join(' · ')
    const detail = [`${from}–${to}`, booking.kind === 'work' ? booking.vehicle?.label : null].filter(Boolean).join(' · ')
    const replan = booking.replan !== null
    return {
      id: booking.id,
      startMinute,
      endMinute,
      title,
      detail,
      tone: booking.kind === 'work' ? 'booking' : booking.kind === 'block' ? 'block' : 'slot',
      flagLabel: replan ? t('planner.replan') : undefined,
      ariaLabel: t(replan ? 'planner.itemAriaReplan' : 'planner.itemAria', { row, kind, title, from, to }),
      onActivate: canWrite ? () => setOpened(booking) : undefined,
    }
  }
  const rowOf = (resource: ResourceRead, subtitle?: string) => {
    const title = resource.name ?? resource.userLabel ?? ''
    return {
      id: resource.id,
      title,
      subtitle,
      unavailable: offDuty(resource, weekday),
      // A booking on several resources shows in each of their rows, its name saying which row.
      items: bookings.filter((b) => b.resources.some((r) => r.resourceId === resource.id)).map((b) => itemOf(b, title)),
    }
  }
  const subtitleOf = (resource: ResourceRead, type?: string) =>
    resource.isActive ? type : [t('planner.inactive'), type].filter(Boolean).join(' · ')
  const groups: DayGridGroup[] = [
    {
      id: 'people',
      label: t('planner.groups.people'),
      rows: people.map((r) =>
        rowOf(r, subtitleOf(r, entryName(technicianTypes.data?.items.find((x) => x.id === r.technicianTypeId), language))),
      ),
    },
    {
      id: 'workplaces',
      label: t('planner.groups.workplaces'),
      rows: workplaces.map((r) =>
        rowOf(r, subtitleOf(r, entryName(workplaceTypes.data?.items.find((x) => x.id === r.workplaceTypeId), language))),
      ),
    },
  ].filter((g) => g.rows.length > 0)

  // The axis: 07:00–18:00, widened to every booking and working hour of the day, in whole hours.
  const edges = [
    ...groups.flatMap((g) => g.rows.flatMap((r) => r.items.flatMap((i) => [i.startMinute, i.endMinute]))),
    ...people.flatMap((r) => {
      const d = r.workingHours.find((h) => h.weekday === weekday)
      return d ? [minutesOf(d.startsAt), minutesOf(d.secondEndsAt ?? d.endsAt)] : []
    }),
  ]
  const fromMinute = Math.floor(Math.min(7 * 60, ...edges) / 60) * 60
  const toMinute = Math.min(DAY_MINUTES, Math.ceil(Math.max(18 * 60, ...edges) / 60) * 60)
  const nowMinute = day === zurichToday() ? minutesOf(wallClock(new Date()).time) : null

  const dayLabel = useMemo(() => formatLongDateOnly(day, `${language.slice(0, 2)}-CH`), [day, language])

  const loading = locations.isLoading || resources.isLoading || plannerDay.isLoading
  const loadError = locations.isError || resources.isError || plannerDay.isError
  const activePeople = personOptions.length
  const activeWorkplaces = workplaceOptions.length
  const setupKey =
    resources.data && activePeople === 0 && activeWorkplaces === 0
      ? 'planner.setup.nothing'
      : resources.data && activeWorkplaces === 0
        ? 'planner.setup.noWorkplace'
        : resources.data && activePeople === 0
          ? 'planner.setup.noPerson'
          : null

  return (
    <Stack gap="md">
      <Group justify="space-between" align="flex-start">
        <div>
          <Title order={2}>{t('planner.title')}</Title>
          <Text c="dimmed" fz="sm">
            {t('planner.subtitle')}
          </Text>
        </div>
        {canWrite && (
          <Button leftSection={<Ban size={16} />} onClick={() => setBlocking(true)} disabled={!locationId || setupKey === 'planner.setup.nothing'}>
            {t('planner.blockResource')}
          </Button>
        )}
      </Group>

      <Group gap="sm" wrap="wrap">
        <Tooltip label={t('planner.previousDay')}>
          <ActionIcon variant="default" size="lg" aria-label={t('planner.previousDay')} onClick={() => go({ date: shiftDay(day, -1) })}>
            <ChevronLeft size={18} />
          </ActionIcon>
        </Tooltip>
        <Tooltip label={t('planner.nextDay')}>
          <ActionIcon variant="default" size="lg" aria-label={t('planner.nextDay')} onClick={() => go({ date: shiftDay(day, 1) })}>
            <ChevronRight size={18} />
          </ActionIcon>
        </Tooltip>
        <Button variant="default" onClick={() => go({ date: zurichToday() })}>
          {t('planner.today')}
        </Button>
        <Text fw={600} aria-live="polite">
          {dayLabel}
        </Text>
        <Select
          aria-label={t('planner.location')}
          placeholder={t('planner.location')}
          data={[
            ...activeLocations.map((l) => ({ value: l.id, label: l.name })),
            // A deactivated location named in the URL stays readable, and says so.
            ...(location && !location.isActive ? [{ value: location.id, label: `${location.name} (${t('planner.inactive')})` }] : []),
          ]}
          value={locationId ?? null}
          onChange={(value) => value && go({ location: value })}
          allowDeselect={false}
          w={240}
          ml="auto"
        />
      </Group>

      {loadError && (
        <Alert color="red" icon={<CircleAlert size={16} />} role="alert">
          <Group justify="space-between">
            <Text size="sm">{t('planner.loadError')}</Text>
            <Button size="xs" variant="light" onClick={() => void refresh()}>
              {t('planner.retry')}
            </Button>
          </Group>
        </Alert>
      )}
      {locations.data && activeLocations.length === 0 && (
        <Alert color="blue" icon={<Info size={16} />}>
          {t('planner.noLocation')}
        </Alert>
      )}
      {setupKey && (
        <Alert color="blue" icon={<Info size={16} />}>
          <Stack gap="xs">
            <Text size="sm">{t(setupKey)}</Text>
            <Text size="sm">{t('planner.setup.where')}</Text>
            <Group>
              <Button size="xs" variant="light" leftSection={<CalendarCog size={14} />} component={Link} to="/administration/planner-resources">
                {t('planner.setup.open')}
              </Button>
            </Group>
          </Stack>
        </Alert>
      )}
      {plannerDay.data && plannerDay.data.gridMinutes === null && (
        <Alert color="yellow" icon={<SlidersHorizontal size={16} />}>
          <Stack gap="xs">
            <Text size="sm">{t('planner.gridNotSet')}</Text>
            <Group>
              <Button size="xs" variant="light" component={Link} to="/administration/settings/planning">
                {t('planner.openSettings')}
              </Button>
            </Group>
          </Stack>
        </Alert>
      )}
      {marked !== null && marked > 0 && (
        <Alert color="yellow" role="status">
          {t('planner.block.marked', { count: marked })}
        </Alert>
      )}

      {loading ? (
        <Skeleton height={320} radius="md" />
      ) : (
        groups.length > 0 && (
          <ResourceDayGrid
            fromMinute={fromMinute}
            toMinute={toMinute}
            gridMinutes={plannerDay.data?.gridMinutes ?? null}
            nowMinute={nowMinute}
            groups={groups}
            ariaLabel={t('planner.grid.aria', { day: dayLabel, location: location?.name ?? '' })}
            labels={{
              resource: t('planner.grid.resource'),
              unavailable: t('planner.grid.unavailable'),
              now: t('planner.grid.now'),
              hour: clockOf,
            }}
          />
        )
      )}

      <BookingDialog
        booking={opened}
        planningPath={planningPath}
        people={personOptions}
        workplaces={workplaceOptions}
        resourceLabels={resourceLabels}
        onClose={() => setOpened(null)}
        onSaved={() => void refresh()}
      />
      <BlockResourceDialog
        opened={blocking}
        day={day}
        planningPath={planningPath}
        people={personOptions}
        workplaces={workplaceOptions}
        onClose={() => setBlocking(false)}
        onSaved={(count) => {
          setMarked(count)
          void refresh()
        }}
      />
    </Stack>
  )
}
