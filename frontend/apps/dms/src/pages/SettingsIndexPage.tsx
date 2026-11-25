import { Alert, Anchor, Paper, Skeleton, Stack, Table, Text, Title } from '@mantine/core'
import { SlidersHorizontal } from 'lucide-react'
import { useTranslation } from 'react-i18next'
import { Link } from 'react-router-dom'
import { slate, useSetBreadcrumb } from '@nexotec/ui-kit'
import { useAuth } from '../auth/AuthContext'
import { useSettingAreas } from '../components/settings/useSettings'

/**
 * Dealer Administration › Einstellungen (KAN-805): the areas whose settings
 * this Betrieb configures — those of the modules it holds, and those of no
 * module (A-RULE-0: one administration surface). Each opens its area screen.
 * Any role reads.
 */
export function SettingsIndexPage() {
  const { t } = useTranslation()
  useSetBreadcrumb([t('shell.nav.administration'), t('settings.title')])
  const { activeDealership } = useAuth()
  const query = useSettingAreas(activeDealership?.id ?? '')
  const areas = query.data?.items ?? []

  return (
    <Stack gap="md">
      <div>
        <Title order={2}>{t('settings.title')}</Title>
        <Text c="dimmed" fz="sm">
          {t('settings.subtitle')}
        </Text>
      </div>
      {query.isError && (
        <Alert color="red" role="alert">
          {t('settings.loadError')}
        </Alert>
      )}
      {query.isLoading && <Skeleton height={160} radius="md" />}
      {query.data && areas.length === 0 && (
        <Paper withBorder radius="md" p="xl">
          <Stack align="center" gap="xs">
            <SlidersHorizontal size={24} color={slate[4]} />
            <Text size="sm" fw={600}>
              {t('settings.empty.title')}
            </Text>
            <Text size="sm" c="dimmed" ta="center">
              {t('settings.empty.description')}
            </Text>
          </Stack>
        </Paper>
      )}
      {areas.length > 0 && (
        <Paper withBorder radius="md">
          <Table striped highlightOnHover aria-label={t('settings.areasLabel')}>
            <Table.Thead>
              <Table.Tr>
                <Table.Th>{t('settings.columns.area')}</Table.Th>
                <Table.Th style={{ textAlign: 'right' }}>{t('settings.columns.count')}</Table.Th>
              </Table.Tr>
            </Table.Thead>
            <Table.Tbody>
              {areas.map((area) => (
                <Table.Tr key={area.area}>
                  <Table.Td>
                    <Anchor component={Link} to={`/administration/settings/${encodeURIComponent(area.area)}`} fz="sm">
                      {t(`settings.areas.${area.area}`)}
                    </Anchor>
                  </Table.Td>
                  <Table.Td style={{ textAlign: 'right' }}>{area.settingCount}</Table.Td>
                </Table.Tr>
              ))}
            </Table.Tbody>
          </Table>
        </Paper>
      )}
    </Stack>
  )
}
