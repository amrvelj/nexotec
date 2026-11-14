import { Navigate } from 'react-router-dom'
import { Stack, Title } from '@mantine/core'
import { useTranslation } from 'react-i18next'
import { useSetBreadcrumb } from '@nexotec/ui-kit'
import { useAuth } from '../auth/AuthContext'
import { IntegrationDealerView } from './IntegrationDealerView'
import { IntegrationPlatformView } from './IntegrationPlatformView'

/** The dealer's integrations are a section of Dealer Administration (KAN-222, A-RULE-0, FR-A-19). */
export const ADMINISTRATION_INTEGRATIONS_PATH = '/administration/integrations'
/** Platform staff's view of every connection, unchanged in function (KAN-222). */
export const PLATFORM_PROVIDERS_PATH = '/platform/providers'

function useIsPlatformAdmin(): boolean {
  const { user } = useAuth()
  return user?.accessRoles.includes('platform_admin') ?? false
}

/**
 * `/administration/integrations` — Dealer Administration › Integrationen:
 * the Betrieb's connections as cards by category. Manager-only at the API
 * (`integration_connections`); the entry is never role-hidden (A-RULE-7).
 * Platform staff work across Betriebe, so they are sent to
 * `/platform/providers`, as `/integrations` always showed them the
 * platform view.
 */
export function AdministrationIntegrationsPage() {
  const { t } = useTranslation()
  useSetBreadcrumb([t('shell.nav.administration'), t('integrationsList.title')])
  if (useIsPlatformAdmin()) return <Navigate to={PLATFORM_PROVIDERS_PATH} replace />
  return (
    <Stack gap="md">
      <Title order={2}>{t('integrationsList.title')}</Title>
      <IntegrationDealerView />
    </Stack>
  )
}

/**
 * `/platform/providers` — every connection across every Betrieb, for
 * platform staff (the grid the old `/integrations` showed them). The
 * endpoints underneath enforce the role whatever this renders.
 */
export function PlatformProvidersPage() {
  const { t } = useTranslation()
  useSetBreadcrumb([t('shell.nav.administration'), t('shell.nav.providers')])
  return (
    <Stack gap="md">
      <Title order={2}>{t('shell.nav.providers')}</Title>
      <IntegrationPlatformView />
    </Stack>
  )
}

/** The old `/integrations` route: redirects to where the screen lives now. */
export function IntegrationsRedirect() {
  return <Navigate to={useIsPlatformAdmin() ? PLATFORM_PROVIDERS_PATH : ADMINISTRATION_INTEGRATIONS_PATH} replace />
}
