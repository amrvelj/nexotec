import { useMemo, useState } from 'react'
import { Alert, Button, Group, Stack, Text, Title } from '@mantine/core'
import { useQuery } from '@tanstack/react-query'
import { useTranslation } from 'react-i18next'
import { Link, useNavigate } from 'react-router-dom'
import { MapPin, Plus } from 'lucide-react'
import {
  Badge,
  ColumnConfigPanel,
  DataGrid,
  defaultColumnLayout,
  useSetBreadcrumb,
  type ColumnRegistryEntry,
  type GridColumnDef,
} from '@nexotec/ui-kit'
import { api } from '../api/client'
import type { HolidayCalendarList, LocationList, LocationRead } from '../api/types'
import { useAuth } from '../auth/AuthContext'
import { useCanWrite } from '../auth/useCanWrite'
import { LocationCreateDialog, DeactivateLocationDialog } from '../components/locations/LocationDialogs'
import { addressLine, holidayCalendarsKey, labelIn, locationsKey } from '../components/locations/locationOptions'
import { useLocationRowMenu } from '../components/locations/useLocationRowMenu'
import { useColumnPanelLabels } from '../hooks/useColumnPanelLabels'
import { useDataGridLabels } from '../hooks/useDataGridLabels'
import { useGridPreferences } from '../hooks/useGridPreferences'
import { useUiPreferencesContext } from '../hooks/UiPreferencesContext'
import { toSwissLocale, type SupportedLanguage } from '../i18n'
import { formatDateOnly, formatDateTime } from '../utils/format'

const GRID_KEY = 'administration.locations.list'

/**
 * Dealer Administration › Locations (KAN-204; PRD Locations, D-A-02,
 * FR-A-03, A-RULE-1): the Betrieb's sites, active and deactivated, each
 * opening its own screen. Any role reads; the manager creates, deactivates
 * and activates (the actions are hidden from a role without the write,
 * KAN-430). Every stored field is a column (ADR-060), a subset visible by
 * default, the layout the user's own (Columns panel). A Betrieb has a
 * handful of sites, so the grid holds them all in the API's name order — no
 * paging, no server sort.
 */
export function LocationsListPage() {
  const { t, i18n } = useTranslation()
  useSetBreadcrumb([t('shell.nav.administration'), t('locations.title')])
  const navigate = useNavigate()
  const { activeDealership } = useAuth()
  const dealershipId = activeDealership?.id ?? ''
  const canWrite = useCanWrite('locations')
  const { density } = useUiPreferencesContext()
  const labels = useDataGridLabels()
  const columnPanelLabels = useColumnPanelLabels()
  const gridPrefs = useGridPreferences(GRID_KEY, { sort: [] })
  const locale = toSwissLocale(i18n.language as SupportedLanguage)
  const [creating, setCreating] = useState(false)

  const locations = useQuery({
    queryKey: locationsKey(dealershipId),
    queryFn: () => api.get<LocationList>(`/dealerships/${dealershipId}/locations`),
    enabled: dealershipId !== '',
  })
  const calendars = useQuery({ queryKey: holidayCalendarsKey, queryFn: () => api.get<HolidayCalendarList>('/holiday-calendars') })
  const rows = useMemo(() => locations.data?.items ?? [], [locations.data])
  const activeCount = rows.filter((r) => r.isActive).length
  const menu = useLocationRowMenu(dealershipId, activeCount)

  const calendarLabel = useMemo(() => {
    const byId = new Map((calendars.data?.items ?? []).map((c) => [c.id, labelIn(c, i18n.language)]))
    return (id: string | null) => (id ? byId.get(id) ?? '' : '')
  }, [calendars.data, i18n.language])

  const columns: GridColumnDef<LocationRead>[] = useMemo(
    () => [
      {
        id: 'name',
        header: t('locations.columns.name'),
        cell: ({ row }) => row.original.name,
        meta: { locked: true, columnLabel: t('locations.columns.name') },
      },
      { id: 'code', header: t('locations.columns.code'), cell: ({ row }) => row.original.code ?? '—', meta: { mono: true } },
      {
        id: 'types',
        header: t('locations.columns.types'),
        cell: ({ row }) => row.original.locationTypes.map((type) => t(`locations.types.${type}`)).join(', ') || '—',
      },
      { id: 'address', header: t('locations.columns.address'), cell: ({ row }) => addressLine(row.original) || '—' },
      { id: 'canton', header: t('locations.columns.canton'), cell: ({ row }) => row.original.addressCanton ?? '—' },
      {
        id: 'country',
        header: t('locations.columns.country'),
        cell: ({ row }) => row.original.addressCountry ?? '—',
        meta: { defaultVisible: false },
      },
      { id: 'phone', header: t('locations.columns.phone'), cell: ({ row }) => row.original.phone ?? '—', meta: { mono: true } },
      {
        id: 'contactPerson',
        header: t('locations.columns.contactPerson'),
        cell: ({ row }) => row.original.contactPerson ?? '—',
        meta: { defaultVisible: false },
      },
      { id: 'email', header: t('locations.columns.email'), cell: ({ row }) => row.original.email ?? '—', meta: { defaultVisible: false } },
      { id: 'leadEmail', header: t('locations.columns.leadEmail'), cell: ({ row }) => row.original.leadEmail ?? '—' },
      {
        id: 'geo',
        header: t('locations.columns.geo'),
        cell: ({ row }) =>
          row.original.geoLat !== null && row.original.geoLng !== null ? `${row.original.geoLat.toFixed(6)}, ${row.original.geoLng.toFixed(6)}` : '—',
        meta: { mono: true, defaultVisible: false },
      },
      {
        id: 'holidayCalendar',
        header: t('locations.columns.holidayCalendar'),
        cell: ({ row }) => calendarLabel(row.original.holidayCalendarId) || '—',
      },
      {
        id: 'letterhead',
        header: t('locations.columns.letterhead'),
        cell: ({ row }) => row.original.letterheadAddressOverride?.replace(/\s*\n\s*/g, ', ') ?? '—',
        meta: { defaultVisible: false },
      },
      {
        id: 'validFrom',
        header: t('locations.columns.validFrom'),
        cell: ({ row }) => (row.original.validFrom ? formatDateOnly(row.original.validFrom, locale) : '—'),
      },
      {
        id: 'validTo',
        header: t('locations.columns.validTo'),
        cell: ({ row }) => (row.original.validTo ? formatDateOnly(row.original.validTo, locale) : '—'),
      },
      {
        id: 'status',
        header: t('locations.columns.status'),
        cell: ({ row }) =>
          row.original.isActive ? (
            <Badge tone="success" dot>
              {t('locations.status.active')}
            </Badge>
          ) : (
            <Badge tone="slate" dot>
              {t('locations.status.inactive')}
            </Badge>
          ),
      },
      {
        id: 'updatedAt',
        header: t('locations.columns.updatedAt'),
        cell: ({ row }) => formatDateTime(row.original.updatedAt, locale),
        meta: { defaultVisible: false },
      },
    ],
    [t, calendarLabel, locale],
  )

  const columnRegistry: ColumnRegistryEntry[] = useMemo(
    () =>
      columns.map((c) => ({
        id: String(c.id),
        label: typeof c.header === 'string' ? c.header : String(c.id),
        defaultVisible: c.meta?.defaultVisible ?? true,
        locked: c.meta?.locked,
      })),
    [columns],
  )
  const columnLayout = useMemo(() => gridPrefs.columnLayout ?? defaultColumnLayout(columnRegistry), [gridPrefs.columnLayout, columnRegistry])

  return (
    <Stack gap="md">
      <Group justify="space-between" align="flex-start">
        <div>
          <Title order={2}>{t('locations.title')}</Title>
          <Text c="dimmed" fz="sm">
            {t('locations.subtitle')}
          </Text>
        </div>
        <Group gap="sm">
          <ColumnConfigPanel registry={columnRegistry} layout={columnLayout} onLayoutChange={gridPrefs.setColumnLayout} labels={columnPanelLabels} />
          {canWrite && (
            <Button leftSection={<Plus size={16} />} onClick={() => setCreating(true)}>
              {t('locations.new')}
            </Button>
          )}
        </Group>
      </Group>
      {locations.isError && (
        <Alert color="red" role="alert">
          {t('locations.loadError')}
        </Alert>
      )}
      {menu.error && menu.pending === null && (
        <Alert color="red" role="alert" withCloseButton closeButtonLabel={t('common.dismiss')} onClose={menu.clearError}>
          {menu.error}
        </Alert>
      )}
      <DataGrid<LocationRead>
        columns={columns}
        rows={rows}
        getRowId={(row) => row.id}
        sort={[]}
        onSortChange={() => {}}
        density={density}
        rowHref={(row) => `/administration/locations/${row.id}`}
        linkComponent={Link}
        loading={locations.isLoading}
        fetchingNextPage={false}
        hasNextPage={false}
        onLoadMore={() => {}}
        total={rows.length}
        totalIsEstimate={false}
        isFiltered={false}
        labels={labels}
        emptyState={{ icon: <MapPin size={24} />, title: t('locations.empty.title'), description: t('locations.empty.description') }}
        rowActions={menu.groups}
        columnLayout={columnLayout}
        onColumnLayoutChange={gridPrefs.setColumnLayout}
      />
      <LocationCreateDialog
        opened={creating}
        onClose={() => setCreating(false)}
        dealershipId={dealershipId}
        onCreated={(created) => {
          setCreating(false)
          navigate(`/administration/locations/${created.id}`)
        }}
      />
      <DeactivateLocationDialog location={menu.pending} onCancel={menu.cancel} onConfirm={menu.confirm} saving={menu.saving} error={menu.error} />
    </Stack>
  )
}
