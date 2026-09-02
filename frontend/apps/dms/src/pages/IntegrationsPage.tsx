import { Stack, Title } from '@mantine/core'
import { useTranslation } from 'react-i18next'
import { useSetBreadcrumb } from '@nexotec/ui-kit'
import { useAuth } from '../auth/AuthContext'
import { IntegrationDealerView } from './IntegrationDealerView'
import { IntegrationPlatformView } from './IntegrationPlatformView'

/**
 * WP-6 PR-7 — the `/integrations` route. "A connection is an entity
 * like any other; its screen is not special" (no bespoke design in the
 * UI/UX Specification, no route in the reference prototype). Dealer or
 * platform view is chosen by the principal's own roles — a layout
 * choice; the endpoints underneath still enforce via `require_read`/
 * `require_access_role` regardless of what this component renders (a
 * platform_admin who somehow reached the dealer view would still only
 * ever see 403s trying anything platform-only, and vice versa).
 */
export function IntegrationsPage() {
  const { t } = useTranslation()
  useSetBreadcrumb([t('shell.nav.masterData'), t('integrationsList.title')])
  const { user } = useAuth()
  const isPlatformAdmin = user?.accessRoles.includes('platform_admin') ?? false

  return (
    <Stack gap="md">
      <Title order={2}>{t('integrationsList.title')}</Title>
      {isPlatformAdmin ? <IntegrationPlatformView /> : <IntegrationDealerView />}
    </Stack>
  )
}
