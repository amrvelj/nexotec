import { Alert, SimpleGrid, Skeleton, Stack, Text } from '@mantine/core'
import { useQuery } from '@tanstack/react-query'
import { useTranslation } from 'react-i18next'
import { useParams } from 'react-router-dom'
import { MapPin } from 'lucide-react'
import { Badge, DetailHeader, useSetBreadcrumb } from '@nexotec/ui-kit'
import { api, ApiError } from '../api/client'
import type { LocationList, LocationRead } from '../api/types'
import { useAuth } from '../auth/AuthContext'
import { useCanWrite } from '../auth/useCanWrite'
import { HolidaysClosuresSection } from '../components/locations/HolidaysClosuresSection'
import { DeactivateLocationDialog } from '../components/locations/LocationDialogs'
import { locationKey, locationsKey } from '../components/locations/locationOptions'
import { AddressSection, ContactsSection, MasterDataSection, OverridesSection } from '../components/locations/LocationSections'
import { OpeningHoursSection } from '../components/locations/OpeningHoursSection'
import { useLocationRowMenu } from '../components/locations/useLocationRowMenu'

/**
 * Dealer Administration › Locations › one location (KAN-204): the identity
 * header (name, code, status; the overflow is the list's row menu — ADR-061)
 * and one section per concern, each saving on its own with the location's
 * If-Match: master data, address and map pin, contacts, opening hours,
 * holidays and closures, and the overrides (collapsed). A role without the
 * write reads every section; the save buttons and actions are not offered.
 */
export function LocationDetailPage() {
  const { t } = useTranslation()
  const { locationId = '' } = useParams()
  const { activeDealership } = useAuth()
  const dealershipId = activeDealership?.id ?? ''
  const canWrite = useCanWrite('locations')

  const location = useQuery({
    queryKey: locationKey(dealershipId, locationId),
    queryFn: () => api.get<LocationRead>(`/dealerships/${dealershipId}/locations/${locationId}`),
    enabled: dealershipId !== '' && locationId !== '',
    retry: false,
  })
  // The list says whether this is the last active site (FR-A-03's disabled entry).
  const all = useQuery({
    queryKey: locationsKey(dealershipId),
    queryFn: () => api.get<LocationList>(`/dealerships/${dealershipId}/locations`),
    enabled: dealershipId !== '',
  })
  const activeCount = (all.data?.items ?? []).filter((l) => l.isActive).length
  const menu = useLocationRowMenu(dealershipId, activeCount)
  useSetBreadcrumb([t('shell.nav.administration'), t('locations.title'), location.data?.name ?? ''])

  if (location.isError) {
    const notFound = location.error instanceof ApiError && location.error.status === 404
    return (
      <Alert color="red" role="alert">
        {notFound ? t('locations.notFound') : t('locations.loadError')}
      </Alert>
    )
  }
  if (!location.data) return <Skeleton height={240} radius="md" />

  const data = location.data
  const groups = menu.groups(data)
  // ADR-061: the primary is the row menu's own deactivate / activate entry —
  // its label and disabled state, and the very action the entry runs.
  const toggle = groups.edit?.[0]
  const runToggle = () => menu.toggleActive(data)
  return (
    <Stack gap="md">
      <DetailHeader
        entityMark={<MapPin size={28} />}
        title={data.name}
        businessKey={data.code ?? '—'}
        badges={
          data.isActive ? (
            <Badge tone="success" dot>
              {t('locations.status.active')}
            </Badge>
          ) : (
            <Badge tone="slate" dot>
              {t('locations.status.inactive')}
            </Badge>
          )
        }
        primaryAction={
          toggle ? { label: toggle.label, icon: toggle.icon, onClick: runToggle, disabled: toggle.disabled, disabledReason: toggle.disabledReason } : undefined
        }
        overflowActions={groups}
        labels={{ moreActions: t('common.moreActions') }}
      />
      {!canWrite && (
        <Text c="dimmed" fz="sm">
          {t('locations.readOnly')}
        </Text>
      )}
      {menu.error && menu.pending === null && (
        <Alert color="red" role="alert" withCloseButton closeButtonLabel={t('common.dismiss')} onClose={menu.clearError}>
          {menu.error}
        </Alert>
      )}
      <SimpleGrid cols={{ base: 1, lg: 2 }} spacing="md">
        <MasterDataSection location={data} dealershipId={dealershipId} canWrite={canWrite} />
        <ContactsSection location={data} dealershipId={dealershipId} canWrite={canWrite} />
      </SimpleGrid>
      <AddressSection location={data} dealershipId={dealershipId} canWrite={canWrite} />
      <OpeningHoursSection location={data} dealershipId={dealershipId} canWrite={canWrite} />
      <HolidaysClosuresSection location={data} dealershipId={dealershipId} canWrite={canWrite} />
      <OverridesSection location={data} dealershipId={dealershipId} canWrite={canWrite} />
      <DeactivateLocationDialog location={menu.pending} onCancel={menu.cancel} onConfirm={menu.confirm} saving={menu.saving} error={menu.error} />
    </Stack>
  )
}
