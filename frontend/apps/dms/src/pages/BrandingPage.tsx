import { Alert, Grid, Skeleton, Stack, Text, Title } from '@mantine/core'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import { useTranslation } from 'react-i18next'
import { useSetBreadcrumb } from '@nexotec/ui-kit'
import { api } from '../api/client'
import type { BrandingRead } from '../api/types'
import { useAuth } from '../auth/AuthContext'
import { BrandColorsSection } from '../components/branding/BrandColorsSection'
import { BrandingPreview } from '../components/branding/BrandingPreview'
import { LogoPlacementSection } from '../components/branding/LogoPlacementSection'
import { LogoSection } from '../components/branding/LogoSection'

const brandingKey = (dealershipId: string) => ['branding', dealershipId] as const

/**
 * Dealer Administration › Logo and colours (KAN-205; PRD "Logo and brand
 * assets"; A-RULE-0: the one place the Betrieb's branding is configured).
 * Three sections — Logo (the three variants) · Colours (with the contrast
 * verdict) · Where the logo appears — each saving on its own, beside the
 * live preview of the document header. Any role reads; the writes are the
 * manager's (`branding`), offered only to whom the session says may write.
 * Every save hands back the whole record, so the version every next save
 * sends as If-Match — and the preview — follow it.
 */
export function BrandingPage() {
  const { t } = useTranslation()
  useSetBreadcrumb([t('shell.nav.administration'), t('branding.title')])
  const { activeDealership } = useAuth()
  const dealershipId = activeDealership?.id ?? ''
  const queryClient = useQueryClient()

  const branding = useQuery({
    queryKey: brandingKey(dealershipId),
    queryFn: () => api.get<BrandingRead>(`/dealerships/${dealershipId}/branding`),
    enabled: dealershipId !== '',
  })
  const onWritten = (data: BrandingRead) => queryClient.setQueryData(brandingKey(dealershipId), data)

  return (
    <Stack gap="md">
      <div>
        <Title order={2}>{t('branding.title')}</Title>
        <Text c="dimmed" fz="sm">
          {t('branding.subtitle')}
        </Text>
      </div>
      {branding.isError && (
        <Alert color="red" role="alert">
          {t('branding.loadError')}
        </Alert>
      )}
      {branding.isLoading && <Skeleton height={240} radius="md" />}
      {branding.data && (
        <Grid gutter="md">
          <Grid.Col span={{ base: 12, lg: 6 }}>
            <Stack gap="md">
              <LogoSection branding={branding.data} onWritten={onWritten} />
              <BrandColorsSection branding={branding.data} onWritten={onWritten} />
              <LogoPlacementSection branding={branding.data} onWritten={onWritten} />
            </Stack>
          </Grid.Col>
          <Grid.Col span={{ base: 12, lg: 6 }}>
            <BrandingPreview branding={branding.data} />
          </Grid.Col>
        </Grid>
      )}
    </Stack>
  )
}
