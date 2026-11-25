import { useState } from 'react'
import { Alert, Button, Group, Loader, SegmentedControl, Stack, Text } from '@mantine/core'
import { History, RotateCcw } from 'lucide-react'
import { useTranslation } from 'react-i18next'
import { Badge, slate } from '@nexotec/ui-kit'
import { useAuth } from '../../auth/AuthContext'
import type { SettingChangeRead, SettingDeclarationRead, SettingSlotRead } from '../../api/types'
import { toSwissLocale, type SupportedLanguage } from '../../i18n'
import { apiRefusalText } from '../../utils/apiRefusal'
import { formatDateTime } from '../../utils/format'
import { formatSettingValue } from './settingValue'
import { restoreSettingChange, useSettingHistory } from './useSettings'

export interface HistoryScope {
  locationId: string | null
  name: string
  slot: SettingSlotRead
  /** False for a deactivated location: its history is read, nothing restored. */
  writable: boolean
}

const ORIGINS = new Set(['edit', 'restore', 'copy'])

/**
 * KAN-805 — a setting's history (the ticket's Design section): newest first,
 * who, when, the value before and the value after; every role reads it
 * (Anto, 2026-11-24). The manager restores an earlier value; the restore is
 * an entry of its own, and nothing is removed.
 */
export function SettingHistory({
  dealershipId,
  declaration,
  scopes,
  canWrite,
  onRestored,
}: {
  dealershipId: string
  declaration: SettingDeclarationRead
  scopes: HistoryScope[]
  canWrite: boolean
  onRestored: (locationId: string | null) => Promise<void>
}) {
  const { t } = useTranslation()
  const [scopeIndex, setScopeIndex] = useState(0)
  const scope = scopes[scopeIndex] ?? scopes[0]
  return (
    <Stack gap="sm" py="xs">
      {scopes.length > 1 && (
        <SegmentedControl
          size="xs"
          aria-label={t('settings.history.scope')}
          value={String(scopeIndex)}
          onChange={(value) => setScopeIndex(Number(value))}
          data={scopes.map((item, index) => ({ value: String(index), label: item.name }))}
          style={{ alignSelf: 'flex-start' }}
        />
      )}
      <ScopeHistory
        key={scope.locationId ?? 'dealership'}
        dealershipId={dealershipId}
        declaration={declaration}
        scope={scope}
        canWrite={canWrite}
        onRestored={onRestored}
      />
    </Stack>
  )
}

function ScopeHistory({
  dealershipId,
  declaration,
  scope,
  canWrite,
  onRestored,
}: {
  dealershipId: string
  declaration: SettingDeclarationRead
  scope: HistoryScope
  canWrite: boolean
  onRestored: (locationId: string | null) => Promise<void>
}) {
  const { t, i18n } = useTranslation()
  const { user } = useAuth()
  const locale = toSwissLocale(i18n.language as SupportedLanguage)
  const query = useSettingHistory(dealershipId, declaration.key, scope.locationId)
  const [restoring, setRestoring] = useState<string | null>(null)
  const [error, setError] = useState<string | null>(null)
  const items = query.data?.pages.flatMap((page) => page.items) ?? []

  const shown = (isSet: boolean, value: SettingChangeRead['value']) =>
    isSet && value ? formatSettingValue(t, i18n.language, declaration, value) : t('settings.notSet.label')

  // Restoring the value in force changes nothing (the store records no entry
  // for it), so it is not offered — every restore offered adds an entry.
  // A stored value that no longer fits is not «not set»: restoring «not set»
  // over it removes it, so it is offered.
  const sameAsNow = (change: SettingChangeRead) =>
    scope.slot.fitsDeclaration &&
    change.isSet === scope.slot.isSet &&
    JSON.stringify(change.isSet ? change.value : null) === JSON.stringify(scope.slot.isSet ? scope.slot.value : null)

  const restore = async (change: SettingChangeRead) => {
    setRestoring(change.id)
    setError(null)
    try {
      await restoreSettingChange(dealershipId, change.id, scope.slot.version)
      await onRestored(scope.locationId)
    } catch (err) {
      setError(apiRefusalText(t, err, 'settings.history.restoreError'))
    } finally {
      setRestoring(null)
    }
  }

  if (query.isLoading) return <Loader size="sm" />
  if (query.isError) {
    return (
      <Text size="sm" c="red">
        {t('settings.history.loadError')}
      </Text>
    )
  }
  if (items.length === 0) {
    return (
      <Stack align="center" gap="xs" py="md">
        <History size={24} color={slate[4]} />
        <Text size="sm" fw={600}>
          {t('settings.history.empty.title')}
        </Text>
        <Text size="sm" c="dimmed">
          {t('settings.history.empty.description')}
        </Text>
      </Stack>
    )
  }

  return (
    <Stack gap={0} aria-label={t('settings.history.title')} role="list">
      {error && (
        <Alert color="red" role="alert" mb="sm" withCloseButton closeButtonLabel={t('common.dismiss')} onClose={() => setError(null)}>
          {error}
        </Alert>
      )}
      {items.map((change, index) => {
        const actor =
          change.createdBy && change.createdBy === user?.id
            ? t('settings.history.you')
            : (change.createdByName ?? t('settings.history.unknownUser'))
        const current = index === 0
        return (
          <div key={change.id} role="listitem" style={{ display: 'flex', gap: 12 }}>
            <div style={{ display: 'flex', flexDirection: 'column', alignItems: 'center', flexShrink: 0 }}>
              <div style={{ width: 8, height: 8, borderRadius: '50%', backgroundColor: slate[4], marginTop: 6 }} />
              <div style={{ flex: 1, width: 1, backgroundColor: slate[2] }} />
            </div>
            <Stack gap={2} pb="md" style={{ flex: 1 }}>
              <Group gap="xs">
                <Text size="sm" fw={600}>
                  {actor}
                </Text>
                <Badge tone="slate">
                  {t(`settings.history.origin.${ORIGINS.has(change.origin) ? change.origin : 'edit'}`)}
                </Badge>
                {/* A stored value that no longer fits is not the value in force. */}
                {current && scope.slot.fitsDeclaration && <Badge tone="informational">{t('settings.history.current')}</Badge>}
              </Group>
              <Text size="xs" c="dimmed">
                {formatDateTime(change.createdAt, locale)}
              </Text>
              <Text size="sm">
                {shown(change.isSetBefore ?? false, change.valueBefore ?? null)} → {shown(change.isSet, change.value)}
              </Text>
              {canWrite && scope.writable && !current && !sameAsNow(change) && (
                <Button
                  variant="subtle"
                  size="xs"
                  px={0}
                  leftSection={<RotateCcw size={14} />}
                  style={{ alignSelf: 'flex-start' }}
                  loading={restoring === change.id}
                  disabled={restoring !== null}
                  onClick={() => void restore(change)}
                >
                  {t('settings.history.restore', { value: shown(change.isSet, change.value) })}
                </Button>
              )}
            </Stack>
          </div>
        )
      })}
      {query.hasNextPage && (
        <Button
          variant="subtle"
          size="xs"
          style={{ alignSelf: 'flex-start' }}
          loading={query.isFetchingNextPage}
          onClick={() => void query.fetchNextPage()}
        >
          {t('settings.history.more')}
        </Button>
      )}
    </Stack>
  )
}
