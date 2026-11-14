import { useState } from 'react'
import { Alert, Anchor, Card, Grid, Group, List, Select, Skeleton, Stack, Switch, Text, Title } from '@mantine/core'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { CircleCheck, TriangleAlert } from 'lucide-react'
import { useTranslation } from 'react-i18next'
import { useSetBreadcrumb } from '@nexotec/ui-kit'
import { api } from '../api/client'
import type { ProfileValidationRead, PublicStockRead, PublishingProfileRead } from '../api/types'
import { useAuth } from '../auth/AuthContext'
import { useCanWrite } from '../auth/useCanWrite'
import { ImagerySection } from '../components/publishing/ImagerySection'
import { LeadRoutingSection } from '../components/publishing/LeadRoutingSection'
import { FinancingSection, IdentitySection, LegalSection, ListingDefaultsSection } from '../components/publishing/ProfileSections'
import { apiRefusalText } from '../utils/apiRefusal'

const BETRIEB = 'betrieb'

/**
 * Dealer Administration › Publishing (KAN-225; PRD «Publishing profile»,
 * FR-A-04, FR-A-05, FR-A-27, A-RULE-0/2). Everything the Betrieb's website
 * — the first channel, D-A-26 — shows: public identity, legal notices,
 * listing defaults, the financing example (wording pending legal review,
 * A-6), and per location its lead routing and photos. A location may switch
 * on its own override and then differs only where it says so (inherit
 * unless set). The validation panel says, in the dealer's words, what the
 * website still needs; Stock refuses a car on the website until it is empty.
 * Every write sends the screen's one version as If-Match.
 */
export function PublishingPage() {
  const { t } = useTranslation()
  useSetBreadcrumb([t('shell.nav.administration'), t('publishing.title')])
  const { activeDealership } = useAuth()
  const dealershipId = activeDealership?.id ?? ''
  const queryClient = useQueryClient()
  const canWrite = useCanWrite('publishing_profile')
  const [scope, setScope] = useState<string>(BETRIEB)
  const [toggleError, setToggleError] = useState<string | null>(null)

  const profileKey = ['publishing-profile', dealershipId] as const
  const profile = useQuery({
    queryKey: profileKey,
    queryFn: () => api.get<PublishingProfileRead>(`/dealerships/${dealershipId}/publishing-profile`),
    enabled: dealershipId !== '',
  })
  const version = profile.data?.version
  const validation = useQuery({
    queryKey: ['publishing-validation', dealershipId, version],
    queryFn: () => api.get<ProfileValidationRead>(`/dealerships/${dealershipId}/publishing-profile/validation?channel=website`),
    enabled: version !== undefined,
  })
  const publicStock = useQuery({
    queryKey: ['public-stock', dealershipId],
    queryFn: () => api.get<PublicStockRead>(`/dealerships/${dealershipId}/public-stock`),
    enabled: dealershipId !== '',
  })
  const onWritten = (data: PublishingProfileRead) => queryClient.setQueryData(profileKey, data)

  const override = profile.data?.locationOverrides.find((o) => o.locationId === scope)
  const toggle = useMutation({
    mutationFn: (on: boolean) => {
      const path = `/dealerships/${dealershipId}/publishing-profile/locations/${scope}`
      const ifMatch = { 'If-Match': String(profile.data?.version) }
      return on ? api.post<PublishingProfileRead>(path, {}, ifMatch) : api.delete<PublishingProfileRead>(path, ifMatch)
    },
    onSuccess: (data) => {
      setToggleError(null)
      onWritten(data)
    },
    onError: (err) => setToggleError(apiRefusalText(t, err, 'publishing.saveFailed')),
  })

  const data = profile.data
  const routing = data?.leadRouting.find((r) => r.locationId === scope)
  const scopes = [
    { value: BETRIEB, label: t('publishing.scope.betrieb') },
    ...(data?.leadRouting ?? []).map((r) => ({ value: r.locationId, label: r.locationName })),
  ]

  return (
    <Stack gap="md">
      <div>
        <Title order={2}>{t('publishing.title')}</Title>
        <Text c="dimmed" fz="sm">
          {t('publishing.subtitle')}
        </Text>
      </div>
      {profile.isError && (
        <Alert color="red" role="alert">
          {t('publishing.loadError')}
        </Alert>
      )}
      {profile.isLoading && <Skeleton height={240} radius="md" />}
      {data && (
        <Grid gutter="md">
          <Grid.Col span={{ base: 12, lg: 8 }}>
            <Stack gap="md">
              <Card withBorder radius="md" padding="md">
                <Group align="flex-end" gap="md" wrap="wrap">
                  <Select
                    label={t('publishing.scope.label')}
                    data={scopes}
                    value={scope}
                    allowDeselect={false}
                    onChange={(value) => setScope(value ?? BETRIEB)}
                    w={280}
                  />
                  {scope !== BETRIEB && (
                    <Switch
                      label={t('publishing.scope.overrideToggle')}
                      checked={override !== undefined}
                      disabled={!canWrite || toggle.isPending}
                      onChange={(e) => toggle.mutate(e.currentTarget.checked)}
                    />
                  )}
                </Group>
                {scope !== BETRIEB && override === undefined && (
                  <Text fz="sm" c="dimmed" mt="xs">
                    {t('publishing.scope.inherits')}
                  </Text>
                )}
                {toggleError && (
                  <Alert color="red" role="alert" mt="xs">
                    {toggleError}
                  </Alert>
                )}
              </Card>
              {scope === BETRIEB && (
                <>
                  <IdentitySection profile={data} target={BETRIEB} canWrite={canWrite} onWritten={onWritten} />
                  <LegalSection profile={data} canWrite={canWrite} onWritten={onWritten} />
                  <ListingDefaultsSection profile={data} target={BETRIEB} canWrite={canWrite} onWritten={onWritten} />
                  <FinancingSection profile={data} target={BETRIEB} canWrite={canWrite} onWritten={onWritten} />
                </>
              )}
              {scope !== BETRIEB && override && (
                <>
                  <IdentitySection profile={data} target={scope} override={override} canWrite={canWrite} onWritten={onWritten} />
                  <ListingDefaultsSection profile={data} target={scope} override={override} canWrite={canWrite} onWritten={onWritten} />
                  <FinancingSection profile={data} target={scope} override={override} canWrite={canWrite} onWritten={onWritten} />
                </>
              )}
              {scope !== BETRIEB && routing && (
                <>
                  <LeadRoutingSection profile={data} routing={routing} canWrite={canWrite} onWritten={onWritten} />
                  <ImagerySection profile={data} locationId={scope} canWrite={canWrite} onWritten={onWritten} />
                </>
              )}
            </Stack>
          </Grid.Col>
          <Grid.Col span={{ base: 12, lg: 4 }}>
            <ValidationPanel
              validation={validation.data}
              carsOnWebsite={publicStock.data?.total}
              onOpenLocation={(locationId) => setScope(locationId)}
            />
          </Grid.Col>
        </Grid>
      )}
    </Stack>
  )
}

function ValidationPanel({
  validation,
  carsOnWebsite,
  onOpenLocation,
}: {
  validation: ProfileValidationRead | undefined
  carsOnWebsite: number | undefined
  onOpenLocation: (locationId: string) => void
}) {
  const { t } = useTranslation()
  return (
    <Card withBorder radius="md" padding="md" role="region" aria-label={t('publishing.validation.title')}>
      <Stack gap="sm">
        <Title order={4}>{t('publishing.validation.title')}</Title>
        {validation?.ready && (
          <Group gap="xs" c="green">
            <CircleCheck size={18} />
            <Text fz="sm">{t('publishing.validation.ready')}</Text>
          </Group>
        )}
        {validation && !validation.ready && (
          <>
            <Group gap="xs" c="orange">
              <TriangleAlert size={18} />
              <Text fz="sm">{t('publishing.validation.notReady')}</Text>
            </Group>
            <List size="sm" spacing={4}>
              {validation.missing.map((item) => (
                <List.Item key={`${item.code}-${item.locationId ?? ''}`}>
                  {t(`publishingValidation.${item.code}`, { location: item.locationName ?? '' })}
                  {item.locationId && (
                    <>
                      {' '}
                      <Anchor component="button" type="button" fz="sm" onClick={() => onOpenLocation(item.locationId!)}>
                        {t('publishing.validation.openLocation')}
                      </Anchor>
                    </>
                  )}
                </List.Item>
              ))}
            </List>
          </>
        )}
        {carsOnWebsite !== undefined && (
          <Text fz="sm" c="dimmed">
            {t('publishing.validation.carsOnWebsite', { count: carsOnWebsite })}
          </Text>
        )}
        <Text fz="xs" c="dimmed">
          {t('publishing.validation.defaultNote')}
        </Text>
      </Stack>
    </Card>
  )
}
