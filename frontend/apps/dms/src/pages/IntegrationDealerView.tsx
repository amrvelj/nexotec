import { useEffect, useMemo, useState } from 'react'
import { Button, Group, Modal, Select, Stack, Text, TextInput } from '@mantine/core'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import { Plug } from 'lucide-react'
import { useTranslation } from 'react-i18next'
import { ConnectionStatusBadge, KeyValueRow, OverviewCard, RowMenu } from '@nexotec/ui-kit'
import { api } from '../api/client'
import { buildConnectionRowMenu, connectionAccountIdentifier, settableSecretSlots } from '../components/connectionRowMenu'
import { DeleteConnectionModal } from '../components/DeleteConnectionModal'
import { translatedConnectionStatusLabel } from '../integrationOptions'
import { useFieldHint } from '../hooks/useFieldHint'
import { useIdempotencyKey } from '../hooks/useIdempotencyKey'
import { toSwissLocale, type SupportedLanguage } from '../i18n'
import { formatDate, formatNumber } from '../utils/format'
import type {
  IntegrationConnectionPage,
  IntegrationConnectionRead,
  IntegrationProviderPage,
  IntegrationProviderRead,
  IntegrationUsageRead,
} from '../api/types'
import { apiRefusalText } from '../utils/apiRefusal'

/**
 * WP-6 PR-7 — the dealer self-service view: cards by category, one per
 * provider. A provider with no connection yet shows a "Connect" card; a
 * connected one shows its status, account identifier (never a secret —
 * `config` never carries one, only `username`/non-secret settings) and
 * the shared row menu (Test/Secret/Disable/Enable/Delete/View usage — see
 * connectionRowMenu.tsx's own docstring for why this is a plain
 * RowMenuGroups rather than a full detail-screen action triple: no
 * connection has its own drill-in screen in this PR's scope).
 *
 * Usage is shown as "indicative calls-vs-quota and attributed cost"
 * (I-2/I-3) — never a billing artifact; the API's own `UsageRead.
 * indicative` flag is always true today, rendered as a caption.
 */
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
  const [deletingFor, setDeletingFor] = useState<IntegrationConnectionRead | null>(null)

  const invalidate = () => queryClient.invalidateQueries({ queryKey: ['integrations'] })
  // KAN-266 — one key per kind, passed the request: a retry keeps its key and
  // replays the first answer (a test is a logged, possibly billed provider
  // call); every success renews it, so the next click is a new submission.
  const testKey = useIdempotencyKey()
  const toggleKey = useIdempotencyKey()

  const connectionsByProviderId = useMemo(() => {
    const map = new Map<string, IntegrationConnectionRead>()
    for (const c of connectionsQuery.data?.items ?? []) map.set(c.providerId, c)
    return map
  }, [connectionsQuery.data])

  const providersById = useMemo(
    () => new Map((providersQuery.data?.items ?? []).map((p) => [p.id, p] as const)),
    [providersQuery.data]
  )

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
    const path = `/integrations/connections/${connection.id}/test`
    await api.post(path, undefined, testKey.headers([path]))
    testKey.renew()
    await invalidate()
  }

  // Enable and disable require If-Match (the backend refuses them without it).
  const toggleEnabled = async (connection: IntegrationConnectionRead) => {
    const path = `/integrations/connections/${connection.id}/${connection.enabled ? 'disable' : 'enable'}`
    await api.post(path, undefined, { 'If-Match': String(connection.version), ...toggleKey.headers([path]) })
    toggleKey.renew()
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
                  badge={<ConnectionStatusBadge status={connection.status} label={translatedConnectionStatusLabel(t, connection.status)} />}
                >
                  <KeyValueRow label={t('integrationsList.fields.provider')}>{provider.displayName}</KeyValueRow>
                  <KeyValueRow label={t('integrationsList.fields.environment')}>
                    {t(`integrationEnums.environment.${connection.environment}`, connection.environment)}
                  </KeyValueRow>
                  <KeyValueRow label={t('integrationsList.fields.accountIdentifier')}>
                    {connectionAccountIdentifier(connection)}
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
                          onDelete: () => setDeletingFor(connection),
                        }, provider.requiredSecretSlots)}
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
        onCreatedWithoutSecret={() => void invalidate()}
      />
      <UsageModal connection={usageFor} onClose={() => setUsageFor(null)} />
      <RotateSecretModal
        connection={rotatingFor}
        requiredSecretSlots={rotatingFor ? providersById.get(rotatingFor.providerId)?.requiredSecretSlots ?? [] : []}
        onClose={() => setRotatingFor(null)}
        onRotated={() => void invalidate()}
      />
      <DeleteConnectionModal
        connection={deletingFor}
        providerName={deletingFor ? providersById.get(deletingFor.providerId)?.displayName ?? deletingFor.providerCode : ''}
        onClose={() => setDeletingFor(null)}
        onDeleted={() => void invalidate()}
      />
    </Stack>
  )
}

const AUTO_I_DAT_PROVIDER_CODE = 'auto_i_dat'

function ConnectDialog({
  provider,
  onClose,
  onConnected,
  onCreatedWithoutSecret,
}: {
  provider: IntegrationProviderRead | null
  onClose: () => void
  onConnected: () => void
  onCreatedWithoutSecret: () => void
}) {
  const { t } = useTranslation()
  const hint = useFieldHint()
  const [environment, setEnvironment] = useState<'sandbox' | 'production'>('sandbox')
  const [secrets, setSecrets] = useState<Record<string, string>>({})
  const [configValues, setConfigValues] = useState<Record<string, string>>({})
  const [submitting, setSubmitting] = useState(false)
  const [error, setError] = useState<string | null>(null)
  // KAN-603 — the create succeeded and a secret write was refused: the
  // connection exists without that secret. Said as such (never «konnte nicht
  // erstellt werden»), and the dialog closes onto the card, whose «Secret
  // setzen» repairs it.
  const [secretFailure, setSecretFailure] = useState<string | null>(null)
  useEffect(() => setSecretFailure(null), [provider])
  // KAN-266 — the create carries one key per connection attempt: a retry of
  // a failed create replays its first answer. Once the create succeeded the
  // key is renewed (KAN-603): a refused secret write is said as such and the
  // dialog closes onto the card, where «Secret setzen» repairs the connection
  // — never a second create. A new provider gets a new key.
  const createKey = useIdempotencyKey()
  useEffect(() => {
    if (provider) createKey.renew()
  }, [provider, createKey])

  if (!provider) return null

  const submit = async () => {
    setSubmitting(true)
    setError(null)
    let created: IntegrationConnectionRead
    try {
      const body = { providerId: provider.id, displayName: provider.displayName, environment, config: configValues }
      created = await api.post<IntegrationConnectionRead>('/integrations/connections', body, createKey.headers(body))
    } catch (err) {
      setError(apiRefusalText(t, err, 'integrationsList.connectError'))
      setSubmitting(false)
      return
    }
    // The connection exists from here on: a new attempt is a new submission.
    createKey.renew()
    try {
      for (const slot of provider.requiredSecretSlots) {
        const value = secrets[slot]
        if (value) await api.put(`/integrations/connections/${created.id}/secrets/${slot}`, { secretValue: value })
      }
      onConnected()
    } catch (err) {
      setSecretFailure(apiRefusalText(t, err, 'integrationsList.connectModal.secretFailedFallback'))
      onCreatedWithoutSecret()
    } finally {
      setSubmitting(false)
    }
  }

  if (secretFailure !== null) {
    return (
      <Modal opened onClose={onClose} title={t('integrationsList.connectModal.title', { provider: provider.displayName })}>
        <Stack gap="sm">
          <Text size="sm" c="red" role="alert">
            {t('integrationsList.connectModal.secretFailed', { reason: secretFailure })}
          </Text>
          <Text size="sm">{t('integrationsList.connectModal.secretFailedRepair')}</Text>
          <Group justify="flex-end">
            <Button onClick={onClose}>{t('integrationsList.connectModal.toConnection')}</Button>
          </Group>
        </Stack>
      </Modal>
    )
  }

  return (
    <Modal opened onClose={onClose} title={t('integrationsList.connectModal.title', { provider: provider.displayName })}>
      <Stack gap="sm">
        {error && <Text size="sm" c="red">{error}</Text>}
        <Select
          // KAN-489 / KAN-488 — the auto-i-dat test access is a Sandbox
          // connection: the reminder of what its agreement allows (the service
          // account's client id and secret, never the personal token, test
          // environments only). The gateway refuses it on the production server
          // (services/gateway.py).
          {...hint(
            t('integrationsList.fields.environment'),
            provider.providerCode === AUTO_I_DAT_PROVIDER_CODE ? t('integrationsList.connectModal.testAccessHint') : undefined,
          )}
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
            onChange={(e) => {
              // KAN-249 — read before the updater: it may run after the event, when currentTarget is null.
              const value = e.currentTarget.value
              setConfigValues((prev) => ({ ...prev, [key]: value }))
            }}
          />
        ))}
        {provider.requiredSecretSlots.map((slot) => (
          <TextInput
            key={slot}
            label={t(`integrationEnums.secretSlot.${slot}`, slot)}
            type="password"
            value={secrets[slot] ?? ''}
            onChange={(e) => {
              const value = e.currentTarget.value
              setSecrets((prev) => ({ ...prev, [slot]: value }))
            }}
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

// The 30-day sum of cost_units, a DECIMAL(12, 4) column (app/integration/
// models/call_log.py), arrives as a decimal string; shown at that scale so the
// fourth decimal is never rounded away (KAN-164). A double holds it exactly up
// to about 1e11 units, far beyond any indicative usage figure.
const COST_UNITS_SCALE = 4

function formatCostUnits(value: string | null): string {
  return value === null ? '—' : formatNumber(Number(value), COST_UNITS_SCALE)
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
              {formatNumber(usageQuery.data?.callsThisPeriod ?? 0)}
            </KeyValueRow>
            <KeyValueRow label={t('integrationsList.usageModal.cost')}>
              {formatCostUnits(usageQuery.data?.costUnitsThisPeriod ?? null)}
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
  requiredSecretSlots,
  onClose,
  onRotated,
}: {
  connection: IntegrationConnectionRead | null
  requiredSecretSlots: readonly string[]
  onClose: () => void
  onRotated: () => void
}) {
  const { t } = useTranslation()
  const [values, setValues] = useState<Record<string, string>>({})
  const [submitting, setSubmitting] = useState(false)
  const [error, setError] = useState<string | null>(null)
  // KAN-607 — the slots this attempt replaced before a later one was refused,
  // and the slot that was refused.
  const [savedBeforeRefusal, setSavedBeforeRefusal] = useState<string[]>([])
  const [refusedSlot, setRefusedSlot] = useState<string | null>(null)
  // The modal stays mounted for every connection: closing it, or opening it
  // for another connection, starts clean — no refusal, no typed value from
  // an earlier attempt.
  useEffect(() => {
    setValues({})
    setError(null)
    setSavedBeforeRefusal([])
    setRefusedSlot(null)
  }, [connection?.id])

  if (!connection) return null

  const slotLabel = (slot: string) => t(`integrationEnums.secretSlot.${slot}`, slot)
  // KAN-603 — every slot the provider requires, set or not, so a connection
  // whose first secret write failed can be given it here.
  const slots = settableSecretSlots(connection, requiredSecretSlots)
  const stored = new Set<string>((connection.secretSlots ?? []).map((s) => s.slot))
  const nothingStored = stored.size === 0

  const submit = async () => {
    setSubmitting(true)
    setError(null)
    setSavedBeforeRefusal([])
    setRefusedSlot(null)
    const saved: string[] = []
    let current: string | null = null
    try {
      for (const slot of slots) {
        const value = values[slot]
        if (!value) continue
        current = slot
        await api.put(`/integrations/connections/${connection.id}/secrets/${slot}`, { secretValue: value })
        saved.push(slot)
      }
      onRotated()
      onClose()
    } catch (err) {
      // KAN-607 — a refused write is said in the UI language, the dialog stays
      // open with what was typed, and the slots already replaced are named:
      // their rotation happened even though a later one was refused.
      setError(apiRefusalText(t, err, 'integrationsList.rotateError'))
      setSavedBeforeRefusal(saved)
      setRefusedSlot(current)
      if (saved.length > 0) onRotated()
    } finally {
      setSubmitting(false)
    }
  }

  return (
    <Modal
      opened
      onClose={onClose}
      title={t(nothingStored ? 'integrationsList.rotateModal.titleSet' : 'integrationsList.rotateModal.title', {
        name: connection.displayName,
      })}
    >
      <Stack gap="sm">
        {error && (
          <Stack gap={4} role="alert">
            {/* With slots already saved, the refusal is said as the refused
                slot's: its sentence («nothing was saved») is about that slot. */}
            {savedBeforeRefusal.length > 0 && (
              <Text size="sm">
                {t('integrationsList.rotateModal.savedBeforeRefusal', {
                  slots: savedBeforeRefusal.map(slotLabel).join(', '),
                  refused: refusedSlot ? slotLabel(refusedSlot) : '—',
                })}
              </Text>
            )}
            <Text size="sm" c="red">{error}</Text>
          </Stack>
        )}
        <Text size="xs" c="dimmed">{t('integrationsList.rotateModal.hint')}</Text>
        {slots.map((slot) => (
          <TextInput
            key={slot}
            label={slotLabel(slot)}
            type="password"
            placeholder={stored.has(slot) ? undefined : t('integrationsList.rotateModal.notSet')}
            value={values[slot] ?? ''}
            onChange={(e) => {
              // KAN-249 — read before the updater (see ConnectDialog).
              const value = e.currentTarget.value
              setValues((prev) => ({ ...prev, [slot]: value }))
            }}
          />
        ))}
        <Group justify="flex-end">
          <Button variant="default" onClick={onClose}>{t('common.cancel')}</Button>
          <Button onClick={() => void submit()} loading={submitting}>
            {t(nothingStored ? 'integrationsList.actions.setSecret' : 'integrationsList.actions.rotateSecret')}
          </Button>
        </Group>
      </Stack>
    </Modal>
  )
}
