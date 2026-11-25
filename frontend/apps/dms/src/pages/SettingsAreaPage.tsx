import { Alert, Skeleton, Stack, Text, Title } from '@mantine/core'
import { useTranslation } from 'react-i18next'
import { useParams } from 'react-router-dom'
import { useSetBreadcrumb } from '@nexotec/ui-kit'
import { ApiError } from '../api/client'
import { useAuth } from '../auth/AuthContext'
import { useCanWrite } from '../auth/useCanWrite'
import { SettingRows } from '../components/settings/SettingRows'
import { useSettingArea } from '../components/settings/useSettings'

/**
 * Dealer Administration › Einstellungen › <area> (KAN-805, criterion 6): the
 * generic screen of one area's declared settings, built from the one row
 * component a topic screen embeds too. A change works from now on. Any role
 * reads; the manager writes (`dealership_settings`).
 */
export function SettingsAreaPage() {
  const { t, i18n } = useTranslation()
  const { area = '' } = useParams()
  // An area no bundle names (an unknown path) is titled «Einstellungen», never by a missing key.
  const title = i18n.exists(`settings.areas.${area}`) ? t(`settings.areas.${area}`) : t('settings.title')
  useSetBreadcrumb([t('shell.nav.administration'), { label: t('settings.title'), href: '/administration/settings' }, title])
  const { activeDealership } = useAuth()
  const dealershipId = activeDealership?.id ?? ''
  const canWrite = useCanWrite('dealership_settings')
  const query = useSettingArea(dealershipId, area)
  const notFound = query.error instanceof ApiError && query.error.status === 404

  return (
    <Stack gap="md">
      <div>
        <Title order={2}>{title}</Title>
        <Text c="dimmed" fz="sm">
          {t('settings.areaSubtitle')}
        </Text>
      </div>
      {notFound && (
        <Alert color="blue" variant="light">
          {t('settings.areaNotFound')}
        </Alert>
      )}
      {query.isError && !notFound && (
        <Alert color="red" role="alert">
          {t('settings.loadError')}
        </Alert>
      )}
      {query.isLoading && <Skeleton height={240} radius="md" />}
      {query.data && !canWrite && (
        <Text c="dimmed" fz="sm">
          {t('settings.readOnly')}
        </Text>
      )}
      {query.data && <SettingRows dealershipId={dealershipId} data={query.data} onReload={() => void query.refetch()} />}
    </Stack>
  )
}
