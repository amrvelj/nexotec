import { useState } from 'react'
import { Alert, Button, Group, Modal, Select, Stack, Text } from '@mantine/core'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Plug } from 'lucide-react'
import { useTranslation } from 'react-i18next'
import { Badge, type BadgeTone, KeyValueRow, OverviewCard, slate } from '@nexotec/ui-kit'
import { api } from '../../api/client'
import type {
  BusinessProfileBindingPage,
  BusinessProfileBindingRead,
  BusinessProfileGoogleChoicePage,
  BusinessProfileLinkStartRead,
  LocationList,
  LocationRead,
} from '../../api/types'
import { useAuth } from '../../auth/AuthContext'
import { useCanWrite } from '../../auth/useCanWrite'
import { useIdempotencyKey } from '../../hooks/useIdempotencyKey'
import { toSwissLocale, type SupportedLanguage } from '../../i18n'
import { apiRefusalText } from '../../utils/apiRefusal'
import { formatDate } from '../../utils/format'
import { businessProfileKey, businessProfileNavigation } from './businessProfileLink'

type CardStatus = 'not_connected' | 'pending' | 'active' | 'error'

const TONE: Record<CardStatus, BadgeTone> = {
  not_connected: 'slate',
  pending: 'warning',
  active: 'success',
  error: 'destructive',
}

/**
 * KAN-226 PR-4 (D-A-32, FR-A-35) — the Google Business Profile section of
 * Administration › Integrationen: one card per active location of the
 * Betrieb, with its link to Google (connect, pick the Google location,
 * disconnect) and what the nightly drift check found — the fields Google shows
 * differently from what Nexotec sends, named; and the departments the push
 * could not show at Google. Nexotec's own client is set up by staff; until it
 * is, the section says so and offers nothing. The manager writes
 * (`integration_connections`, never role-hidden: the reads stay).
 */
export function BusinessProfileSection() {
  const { t } = useTranslation()
  const { activeDealership } = useAuth()
  const dealershipId = activeDealership?.id ?? ''
  const canWrite = useCanWrite('integration_connections')

  const bindings = useQuery({
    queryKey: businessProfileKey(dealershipId),
    queryFn: () => api.get<BusinessProfileBindingPage>(`/dealerships/${dealershipId}/business-profile/bindings`),
    enabled: dealershipId !== '',
  })
  const locations = useQuery({
    queryKey: ['dealership', dealershipId, 'locations'],
    queryFn: () => api.get<LocationList>(`/dealerships/${dealershipId}/locations`),
    enabled: dealershipId !== '',
  })

  const live = new Map<string, BusinessProfileBindingRead>()
  for (const binding of bindings.data?.items ?? []) {
    if (binding.status !== 'disconnected') live.set(binding.locationId, binding)
  }
  const sites = (locations.data?.items ?? []).filter((location) => location.isActive || live.has(location.id))

  return (
    <Stack gap="sm" aria-label={t('businessProfile.card.title')} role="region">
      <Text size="sm" fw={600} tt="uppercase" c="dimmed">
        {t('businessProfile.card.title')}
      </Text>
      <Text size="sm" c={slate[5]}>
        {t('businessProfile.card.subtitle')}
      </Text>
      {(bindings.isError || locations.isError) && (
        <Alert color="red" variant="light" role="alert">
          {apiRefusalText(t, bindings.error ?? locations.error, 'businessProfile.card.loadError')}
        </Alert>
      )}
      {bindings.isLoading || locations.isLoading ? (
        <Text size="sm" c="dimmed">
          {t('common.loading')}
        </Text>
      ) : bindings.data && !bindings.data.configured ? (
        <Text size="sm" c="dimmed">
          {t('businessProfile.card.notConfigured')}
        </Text>
      ) : bindings.data && sites.length === 0 ? (
        <Text size="sm" c="dimmed">
          {t('businessProfile.card.noLocations')}
        </Text>
      ) : bindings.data ? (
        <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fill, minmax(320px, 1fr))', gap: 16 }}>
          {sites.map((location) => (
            <LocationCard
              key={location.id}
              dealershipId={dealershipId}
              location={location}
              binding={live.get(location.id) ?? null}
              canWrite={canWrite}
            />
          ))}
        </div>
      ) : null}
    </Stack>
  )
}

function LocationCard({
  dealershipId,
  location,
  binding,
  canWrite,
}: {
  dealershipId: string
  location: LocationRead
  binding: BusinessProfileBindingRead | null
  canWrite: boolean
}) {
  const { t, i18n } = useTranslation()
  const locale = toSwissLocale(i18n.language as SupportedLanguage)
  const queryClient = useQueryClient()
  const idempotency = useIdempotencyKey()
  const [error, setError] = useState<string | null>(null)
  const [picking, setPicking] = useState(false)
  const [confirming, setConfirming] = useState(false)
  const status: CardStatus = binding ? (binding.status as CardStatus) : 'not_connected'
  const refresh = () => queryClient.invalidateQueries({ queryKey: businessProfileKey(dealershipId) })

  const connect = useMutation({
    mutationFn: () => {
      const path = `/dealerships/${dealershipId}/business-profile/links`
      const body = { locationId: location.id }
      return api.post<BusinessProfileLinkStartRead>(path, body, idempotency.headers({ path, body }))
    },
    onSuccess: (started) => {
      idempotency.renew()
      businessProfileNavigation.toGoogle(started.authorizeUrl)
    },
    onError: (err) => setError(apiRefusalText(t, err, 'common.requestFailed')),
  })
  const disconnect = useMutation({
    mutationFn: (current: BusinessProfileBindingRead) =>
      api.delete<void>(`/dealerships/${dealershipId}/business-profile/bindings/${current.id}`, {
        'If-Match': String(current.version),
      }),
    onSuccess: async () => {
      setConfirming(false)
      setError(null)
      await refresh()
    },
    onError: (err) => {
      setConfirming(false)
      setError(apiRefusalText(t, err, 'common.requestFailed'))
      void refresh()
    },
  })

  return (
    <OverviewCard
      title={location.name}
      badge={<Badge tone={TONE[status]} dot>{t(`businessProfile.card.status.${status}`)}</Badge>}
    >
      {error && (
        <Alert color="red" variant="light" role="alert" withCloseButton closeButtonLabel={t('common.dismiss')} onClose={() => setError(null)}>
          {error}
        </Alert>
      )}
      {binding?.googleLocationTitle && (
        <KeyValueRow label={t('businessProfile.card.fields.googleLocation')}>{binding.googleLocationTitle}</KeyValueRow>
      )}
      {binding?.status === 'active' && (
        <KeyValueRow label={t('businessProfile.card.fields.drift')}>
          <DriftText binding={binding} locale={locale} />
        </KeyValueRow>
      )}
      {binding?.status === 'active' && binding.drift?.outcome === 'drift' && (
        <Text size="xs" c={slate[5]}>
          {t('businessProfile.card.drift.hint')}
        </Text>
      )}
      {binding && (binding.unmappedDepartments ?? []).length > 0 && (
        <>
          <KeyValueRow label={t('businessProfile.card.fields.unmapped')}>
            {(binding.unmappedDepartments ?? []).map((d) => t(`businessProfile.card.departments.${d}`, d)).join(', ')}
          </KeyValueRow>
          <Text size="xs" c={slate[5]}>
            {t('businessProfile.card.unmappedHint')}
          </Text>
        </>
      )}
      {status === 'pending' && <Text size="sm">{t('businessProfile.card.pendingText')}</Text>}
      {status === 'error' && <Text size="sm">{t('businessProfile.card.errorText')}</Text>}
      {canWrite && (
        <Group gap="xs" mt="sm">
          {status === 'not_connected' && (
            <Button size="xs" variant="default" leftSection={<Plug size={14} />} loading={connect.isPending} onClick={() => connect.mutate()}>
              {t('businessProfile.card.actions.connect')}
            </Button>
          )}
          {status === 'pending' && (
            <Button size="xs" variant="light" onClick={() => setPicking(true)}>
              {t('businessProfile.card.actions.pick')}
            </Button>
          )}
          {binding && (
            <Button size="xs" variant="subtle" color="red" onClick={() => setConfirming(true)}>
              {t('businessProfile.card.actions.disconnect')}
            </Button>
          )}
        </Group>
      )}
      {binding && picking && (
        <PickGoogleLocationModal
          dealershipId={dealershipId}
          binding={binding}
          onClose={() => setPicking(false)}
          onPicked={async () => {
            setPicking(false)
            await refresh()
          }}
        />
      )}
      <Modal opened={confirming} onClose={() => setConfirming(false)} title={t('businessProfile.card.disconnect.title')}>
        <Stack gap="md">
          <Text size="sm">{t('businessProfile.card.disconnect.body')}</Text>
          <Group justify="flex-end" gap="xs">
            <Button variant="default" onClick={() => setConfirming(false)}>
              {t('common.cancel')}
            </Button>
            <Button color="red" loading={disconnect.isPending} onClick={() => binding && disconnect.mutate(binding)}>
              {t('businessProfile.card.disconnect.confirm')}
            </Button>
          </Group>
        </Stack>
      </Modal>
    </OverviewCard>
  )
}

function DriftText({ binding, locale }: { binding: BusinessProfileBindingRead; locale: string }) {
  const { t } = useTranslation()
  const drift = binding.drift
  if (!drift) return <>{t('businessProfile.card.drift.never')}</>
  const date = formatDate(drift.checkedAt, locale)
  const fields = drift.driftedFields.map((code) => t(`businessProfile.card.fieldNames.${code}`, code)).join(', ')
  return <>{t(`businessProfile.card.drift.${drift.outcome}`, { date, fields })}</>
}

function PickGoogleLocationModal({
  dealershipId,
  binding,
  onClose,
  onPicked,
}: {
  dealershipId: string
  binding: BusinessProfileBindingRead
  onClose: () => void
  onPicked: () => void | Promise<void>
}) {
  const { t } = useTranslation()
  const [choice, setChoice] = useState<string | null>(null)
  const [error, setError] = useState<string | null>(null)
  const base = `/dealerships/${dealershipId}/business-profile/bindings/${binding.id}`
  const choices = useQuery({
    queryKey: [...businessProfileKey(dealershipId), 'choices', binding.id],
    queryFn: () => api.get<BusinessProfileGoogleChoicePage>(`${base}/google-locations`),
  })
  const items = choices.data?.items ?? []
  const pick = useMutation({
    mutationFn: (locationName: string) => {
      const picked = items.find((item) => item.locationName === locationName)
      return api.put<BusinessProfileBindingRead>(
        `${base}/google-location`,
        { accountName: picked?.accountName, locationName },
        { 'If-Match': String(binding.version) }
      )
    },
    onSuccess: () => onPicked(),
    onError: (err) => setError(apiRefusalText(t, err, 'common.requestFailed')),
  })

  return (
    <Modal opened onClose={onClose} title={t('businessProfile.card.pick.title')}>
      <Stack gap="md">
        {(error || choices.isError) && (
          <Alert color="red" variant="light" role="alert">
            {error ?? apiRefusalText(t, choices.error, 'common.requestFailed')}
          </Alert>
        )}
        {choices.isLoading ? (
          <Text size="sm" c="dimmed">
            {t('common.loading')}
          </Text>
        ) : items.length === 0 && !choices.isError ? (
          <Text size="sm">{t('businessProfile.card.pick.empty')}</Text>
        ) : (
          <Select
            label={t('businessProfile.card.pick.label')}
            data={items.map((item) => ({ value: item.locationName, label: `${item.title} · ${item.accountDisplayName}` }))}
            value={choice}
            onChange={setChoice}
          />
        )}
        <Group justify="flex-end" gap="xs">
          <Button variant="default" onClick={onClose}>
            {t('common.cancel')}
          </Button>
          <Button disabled={!choice} loading={pick.isPending} onClick={() => choice && pick.mutate(choice)}>
            {t('businessProfile.card.pick.confirm')}
          </Button>
        </Group>
      </Stack>
    </Modal>
  )
}
