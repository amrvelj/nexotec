import { useMemo, useState } from 'react'
import { Button, Group, Modal, NumberFormatter, Select, Stack, Text, TextInput } from '@mantine/core'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import { Plug } from 'lucide-react'
import { useTranslation } from 'react-i18next'
import { ConnectionStatusBadge, KeyValueRow, OverviewCard, RowMenu } from '@nexotec/ui-kit'
import { api, ApiError } from '../api/client'
import { buildConnectionRowMenu } from '../components/connectionRowMenu'
import { toSwissLocale, type SupportedLanguage } from '../i18n'
import { formatDate } from '../utils/format'
import type {
  IntegrationConnectionPage,
  IntegrationConnectionRead,
  IntegrationProviderPage,
  IntegrationProviderRead,
  IntegrationUsageRead,
} from '../api/types'

/**
 * WP-6 PR-7 — the dealer self-service view: cards by category, one per
 * provider. A provider with no connection yet shows a "Connect" card; a
 * connected one shows its status, account identifier (never a secret —
 * `config` never carries one, only `username`/non-secret settings) and
 * the shared row menu (Test/Rotate/Disable/Enable/View usage — see
 * connectionRowMenu.tsx's own docstring for why this is a plain
 * RowMenuGroups rather than a full detail-screen action triple: no
 * connection has its own drill-in screen in this PR's scope).
 *
 * Usage is shown as "indicative calls-vs-quota and attributed cost"
 * (I-2/I-3) — never a billing artifact; the API's own `UsageRead.
 * indicative` flag is always true today, rendered as a caption.
 */
/**
 * The account-identifier shown on a connected provider's card. auto-i-dat
 * (KAN-36) carries `benutzerNr`/`benutzerInfo` — a dealer can hold several
 * auto-i-dat accounts, distinguished only by these two fields, so they take
 * priority over the plain `username` every other provider falls back to.
 */
function accountIdentifier(connection: IntegrationConnectionRead): string {
  const { benutzerNr, benutzerInfo, username } = connection.config
  if (benutzerNr != null && typeof benutzerInfo === 'string') {
    return `${benutzerNr} · ${benutzerInfo}`
  }
  return typeof username === 'string' ? username : '—'
}

export function IntegrationDealerView() {
  const { t, i18n } = useTranslation()
  const queryClient = useQueryClient()
  const locale = toSwissLocale(i18n.language as SupportedLanguage)

  const providersQuery = useQuery({
    queryKey: ['integrations', 'providers'],
    queryFn: () => api.get<IntegrationProviderPage>('/integrations/providers'),
  })
  const connectionsQuery = useQuery({
    queryKey: ['integrations', 'connections', 'own'],
    queryFn: () => api.get<IntegrationConnectionPage>('/integrations/connections?limit=100'),
  })

  const [connectingProvider, setConnectingProvider] = useState<IntegrationProviderRead | null>(null)
  const [usageFor, setUsageFor] = useState<IntegrationConnectionRead | null>(null)
  const [rotatingFor, setRotatingFor] = useState<IntegrationConnectionRead | null>(null)

  const invalidate = () => queryClient.invalidateQueries({ queryKey: ['integrations'] })

  const connectionsByProviderId = useMemo(() => {
    const map = new Map<string, IntegrationConnectionRead>()
    for (const c of connectionsQuery.data?.items ?? []) map.set(c.providerId, c)
    return map
  }, [connectionsQuery.data])

  const providersByCategory = useMemo(() => {
    const groups = new Map<string, IntegrationProviderRead[]>()
    for (const p of providersQuery.data?.items ?? []) {
      const list = groups.get(p.category) ?? []
      list.push(p)
      groups.set(p.category, list)
    }
    return groups
  }, [providersQuery.data])

  const test = async (connection: IntegrationConnectionRead) => {
    await api.post(`/integrations/connections/${connection.id}/test`)
    await invalidate()
  }

  const toggleEnabled = async (connection: IntegrationConnectionRead) => {
    await api.post(`/integrations/connections/${connection.id}/${connection.enabled ? 'disable' : 'enable'}`)
    await invalidate()
  }

  if (providersQuery.isLoading || connectionsQuery.isLoading) {
    return <Text size="sm" c="dimmed">{t('common.loading')}</Text>
  }

  return (
    <Stack gap="xl">
      {[...providersByCategory.entries()].map(([category, providers]) => (
        <Stack key={category} gap="sm">
          <Text size="sm" fw={600} tt="uppercase" c="dimmed">
            {t(`integrationsList.categories.${category}`, category)}
          </Text>
          <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fill, minmax(320px, 1fr))', gap: 16 }}>
            {providers.map((provider) => {
              const connection = connectionsByProviderId.get(provider.id)
              return connection ? (
                <OverviewCard
                  key={provider.id}
                  title={connection.displayName}
                  badge={<ConnectionStatusBadge status={connection.status} />}
                >
                  <KeyValueRow label={t('integrationsList.fields.provider')}>{provider.displayName}</KeyValueRow>
                  <KeyValueRow label={t('integrationsList.fields.environment')}>
                    {t(`integrationEnums.environment.${connection.environment}`, connection.environment)}
                  </KeyValueRow>
                  <KeyValueRow label={t('integrationsList.fields.accountIdentifier')}>
                    {accountIdentifier(connection)}
                  </KeyValueRow>
                  <KeyValueRow label={t('integrationsList.fields.lastVerified')}>
                    {connection.lastVerifiedAt ? formatDate(connection.lastVerifiedAt, locale) : '—'}
                  </KeyValueRow>
                  <Group justify="space-between" mt="sm">
                    <Button size="xs" variant="light" onClick={() => void test(connection)}>
                      {t('integrationsList.actions.test')}
                    </Button>
                    <Group gap="xs">
                      <Button size="xs" variant="subtle" onClick={() => setUsageFor(connection)}>
                        {t('integrationsList.actions.viewUsage')}
                      </Button>
                      <RowMenu
                        ariaLabel={t('common.rowActionsLabel')}
                        groups={buildConnectionRowMenu(t, connection, {
                          onTest: () => void test(connection),
                          onViewUsage: () => setUsageFor(connection),
                          onRotateSecret: () => setRotatingFor(connection),
                          onToggleEnabled: () => void toggleEnabled(connection),
                        })}
                      />
                    </Group>
                  </Group>
                </OverviewCard>
              ) : (
                <OverviewCard key={provider.id} title={provider.displayName}>
                  <Text size="sm" c="dimmed">{t('integrationsList.notConnected')}</Text>
                  <Button
                    size="xs"
                    variant="default"
                    leftSection={<Plug size={14} />}
                    mt="sm"
                    onClick={() => setConnectingProvider(provider)}
                  >
                    {t('integrationsList.actions.connect')}
                  </Button>
                </OverviewCard>
              )
            })}
          </div>
        </Stack>
      ))}

      <ConnectDialog
        provider={connectingProvider}
        onClose={() => setConnectingProvider(null)}
        onConnected={() => {
          setConnectingProvider(null)
          void invalidate()
        }}
      />
      <UsageModal connection={usageFor} onClose={() => setUsageFor(null)} />
      <RotateSecretModal connection={rotatingFor} onClose={() => setRotatingFor(null)} onRotated={() => void invalidate()} />
    </Stack>
  )
}

function ConnectDialog({
  provider,
  onClose,
  onConnected,
}: {
  provider: IntegrationProviderRead | null
  onClose: () => void
  onConnected: () => void
}) {
  const { t } = useTranslation()
  const [environment, setEnvironment] = useState<'sandbox' | 'production'>('sandbox')
  const [secrets, setSecrets] = useState<Record<string, string>>({})
  const [configValues, setConfigValues] = useState<Record<string, string>>({})
  const [submitting, setSubmitting] = useState(false)
  const [error, setError] = useState<string | null>(null)

  if (!provider) return null

  const submit = async () => {
    setSubmitting(true)
    setError(null)
    try {
      const created = await api.post<IntegrationConnectionRead>('/integrations/connections', {
        providerId: provider.id,
        displayName: provider.displayName,
        environment,
        config: configValues,
      })
      for (const slot of provider.requiredSecretSlots) {
        const value = secrets[slot]
        if (value) await api.put(`/integrations/connections/${created.id}/secrets/${slot}`, { secretValue: value })
      }
      onConnected()
    } catch (err) {
      setError(err instanceof ApiError ? err.message : t('integrationsList.connectError'))
    } finally {
      setSubmitting(false)
    }
  }

  return (
    <Modal opened onClose={onClose} title={t('integrationsList.connectModal.title', { provider: provider.displayName })}>
      <Stack gap="sm">
        {error && <Text size="sm" c="red">{error}</Text>}
        <Select
          label={t('integrationsList.fields.environment')}
          data={[
            { value: 'sandbox', label: t('integrationEnums.environment.sandbox'), disabled: !provider.supportsSandbox },
            { value: 'production', label: t('integrationEnums.environment.production') },
          ]}
          value={environment}
          onChange={(v) => setEnvironment((v as 'sandbox' | 'production') ?? 'sandbox')}
        />
        {provider.requiredConfigKeys.map((key) => (
          <TextInput
            key={key}
            label={t(`integrationEnums.configKey.${key}`, key)}
            value={configValues[key] ?? ''}
            onChange={(e) => setConfigValues((prev) => ({ ...prev, [key]: e.currentTarget.value }))}
          />
        ))}
        {provider.requiredSecretSlots.map((slot) => (
          <TextInput
            key={slot}
            label={t(`integrationEnums.secretSlot.${slot}`, slot)}
            type="password"
            value={secrets[slot] ?? ''}
            onChange={(e) => setSecrets((prev) => ({ ...prev, [slot]: e.currentTarget.value }))}
          />
        ))}
        <Group justify="flex-end">
          <Button variant="default" onClick={onClose}>{t('common.cancel')}</Button>
          <Button onClick={() => void submit()} loading={submitting}>{t('integrationsList.actions.connect')}</Button>
        </Group>
      </Stack>
    </Modal>
  )
}

function UsageModal({ connection, onClose }: { connection: IntegrationConnectionRead | null; onClose: () => void }) {
  const { t } = useTranslation()
  const usageQuery = useQuery({
    queryKey: ['integrations', 'usage', connection?.id],
    queryFn: () => api.get<IntegrationUsageRead>(`/integrations/connections/${connection!.id}/usage`),
    enabled: connection !== null,
  })

  if (!connection) return null

  return (
    <Modal opened onClose={onClose} title={t('integrationsList.usageModal.title', { name: connection.displayName })}>
      <Stack gap="xs">
        {usageQuery.isLoading ? (
          <Text size="sm" c="dimmed">{t('common.loading')}</Text>
        ) : (
          <>
            <KeyValueRow label={t('integrationsList.usageModal.calls')}>
              <NumberFormatter value={usageQuery.data?.callsThisPeriod ?? 0} thousandSeparator="'" />
            </KeyValueRow>
            <KeyValueRow label={t('integrationsList.usageModal.cost')}>
              {usageQuery.data?.costUnitsThisPeriod ?? '—'}
            </KeyValueRow>
            <Text size="xs" c="dimmed">{t('integrationsList.usageModal.indicative')}</Text>
          </>
        )}
      </Stack>
    </Modal>
  )
}

function RotateSecretModal({
  connection,
  onClose,
  onRotated,
}: {
  connection: IntegrationConnectionRead | null
  onClose: () => void
  onRotated: () => void
}) {
  const { t } = useTranslation()
  const [values, setValues] = useState<Record<string, string>>({})
  const [submitting, setSubmitting] = useState(false)

  if (!connection) return null

  const submit = async () => {
    setSubmitting(true)
    try {
      for (const slot of connection.secretSlots ?? []) {
        const value = values[slot.slot]
        if (value) await api.put(`/integrations/connections/${connection.id}/secrets/${slot.slot}`, { secretValue: value })
      }
      onRotated()
      onClose()
    } finally {
      setSubmitting(false)
    }
  }

  return (
    <Modal opened onClose={onClose} title={t('integrationsList.rotateModal.title', { name: connection.displayName })}>
      <Stack gap="sm">
        <Text size="xs" c="dimmed">{t('integrationsList.rotateModal.hint')}</Text>
        {(connection.secretSlots ?? []).map((slot) => (
          <TextInput
            key={slot.slot}
            label={t(`integrationEnums.secretSlot.${slot.slot}`, slot.slot)}
            type="password"
            value={values[slot.slot] ?? ''}
            onChange={(e) => setValues((prev) => ({ ...prev, [slot.slot]: e.currentTarget.value }))}
          />
        ))}
        <Group justify="flex-end">
          <Button variant="default" onClick={onClose}>{t('common.cancel')}</Button>
          <Button onClick={() => void submit()} loading={submitting}>{t('integrationsList.actions.rotateSecret')}</Button>
        </Group>
      </Stack>
    </Modal>
  )
}
