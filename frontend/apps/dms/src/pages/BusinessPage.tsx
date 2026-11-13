import { Alert, Skeleton, Stack, Text, Title } from '@mantine/core'
import { useQuery } from '@tanstack/react-query'
import { useTranslation } from 'react-i18next'
import { useSetBreadcrumb } from '@nexotec/ui-kit'
import { api } from '../api/client'
import type { DealershipRead, DealershipSettingsRead } from '../api/types'
import { useAuth } from '../auth/AuthContext'
import { AddressAutocompleteSection } from '../components/business/AddressAutocompleteSection'
import { AddressesSection } from '../components/business/AddressesSection'
import { BankingSection } from '../components/business/BankingSection'
import { CopyConfigurationSection } from '../components/business/CopyConfigurationSection'
import { FiscalDefaultsSection } from '../components/business/FiscalDefaultsSection'
import { LegalIdentitySection } from '../components/business/LegalIdentitySection'
import { LicencesSection } from '../components/business/LicencesSection'
import { SecuritySection } from '../components/business/SecuritySection'
import { dealershipKey, settingsKey } from '../components/business/queryKeys'

/**
 * Dealer Administration › Business (KAN-203; PRD "Dealership master data";
 * A-RULE-0: the one place the Betrieb's own master data is configured).
 * Eight sections — Legal identity · Addresses · Address entry (KAN-37) ·
 * Banking · Commercial and fiscal defaults · Licences and memberships ·
 * Security (KAN-229: the MFA switch and the session policy) ·
 * Konfiguration übernehmen von … (KAN-220, the manager's only) —
 * each saving on its own.
 * Any role reads; the writes are the manager's at the API (a 403 is said
 * beneath the section, the screen is never role-hidden — A-RULE-7); the
 * Banking section exists only for manager, finance and the auditor
 * (ADR-100: the API answers 404 to anyone else, and the section is absent).
 */
export function BusinessPage() {
  const { t } = useTranslation()
  useSetBreadcrumb([t('shell.nav.administration'), t('business.title')])
  const { activeDealership } = useAuth()
  const dealershipId = activeDealership?.id ?? ''

  const dealership = useQuery({
    queryKey: dealershipKey(dealershipId),
    queryFn: () => api.get<DealershipRead>(`/dealerships/${dealershipId}`),
    enabled: dealershipId !== '',
  })
  const settings = useQuery({
    queryKey: settingsKey(dealershipId),
    queryFn: () => api.get<DealershipSettingsRead>(`/dealerships/${dealershipId}/settings`),
    enabled: dealershipId !== '',
  })

  return (
    <Stack gap="md">
      <div>
        <Title order={2}>{t('business.title')}</Title>
        <Text c="dimmed" fz="sm">
          {t('business.subtitle')}
        </Text>
      </div>
      {(dealership.isError || settings.isError) && (
        <Alert color="red" role="alert">
          {t('business.loadError')}
        </Alert>
      )}
      {dealership.isLoading && <Skeleton height={240} radius="md" />}
      {dealership.data && (
        <>
          <LegalIdentitySection dealership={dealership.data} />
          <AddressesSection dealership={dealership.data} />
          {settings.data && <AddressAutocompleteSection settings={settings.data} />}
          <BankingSection dealershipId={dealershipId} />
          {settings.data && <FiscalDefaultsSection dealership={dealership.data} settings={settings.data} />}
          <LicencesSection dealership={dealership.data} />
          <SecuritySection dealershipId={dealershipId} />
          <CopyConfigurationSection dealershipId={dealershipId} />
        </>
      )}
    </Stack>
  )
}
