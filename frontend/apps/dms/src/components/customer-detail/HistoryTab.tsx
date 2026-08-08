import { Loader, Stack, Text } from '@mantine/core'
import { History } from 'lucide-react'
import { slate } from '@nexotec/ui-kit'
import { useAuth } from '../../auth/AuthContext'
import { formatDateTime } from '../../utils/format'
import type { AuditEventRead } from '../../api/types'

function prettifyKey(key: string): string {
  const spaced = key.replace(/_/g, ' ')
  return spaced.charAt(0).toUpperCase() + spaced.slice(1)
}

function formatValue(v: unknown): string {
  if (v === null || v === undefined) return '—'
  if (typeof v === 'boolean') return v ? 'Yes' : 'No'
  if (typeof v === 'object') return JSON.stringify(v)
  return String(v)
}

// before/after are raw entity snapshots (not a pre-computed diff), per
// AuditEventRead — only the keys that actually changed are worth showing,
// per "a human-readable before → after".
function diff(before: Record<string, unknown> | null, after: Record<string, unknown> | null) {
  const keys = new Set([...Object.keys(before ?? {}), ...Object.keys(after ?? {})])
  const changes: { key: string; before: unknown; after: unknown }[] = []
  for (const key of keys) {
    const b = before?.[key]
    const a = after?.[key]
    if (JSON.stringify(b) !== JSON.stringify(a)) changes.push({ key, before: b, after: a })
  }
  return changes
}

function HistoryEvent({ event, isYou }: { event: AuditEventRead; isYou: boolean }) {
  const changes = diff(event.before, event.after)
  return (
    <div style={{ display: 'flex', gap: 12 }}>
      <div style={{ display: 'flex', flexDirection: 'column', alignItems: 'center', flexShrink: 0 }}>
        <div style={{ width: 8, height: 8, borderRadius: '50%', backgroundColor: slate[4], marginTop: 6 }} />
        <div style={{ flex: 1, width: 1, backgroundColor: slate[2] }} />
      </div>
      <Stack gap={4} pb="md" style={{ flex: 1 }}>
        <Text size="sm">
          <Text component="span" fw={600}>
            {isYou ? 'You' : event.actorId ? `User ${event.actorId.slice(0, 8)}` : 'System'}
          </Text>{' '}
          {event.action} this customer
        </Text>
        <Text size="xs" c="dimmed">
          {formatDateTime(event.createdAt)}
        </Text>
        {changes.length > 0 && (
          <Stack gap={2} mt={4}>
            {changes.map((c) => (
              <Text key={c.key} size="xs" c="dimmed">
                <Text component="span" fw={500} c={slate[7]}>
                  {prettifyKey(c.key)}
                </Text>
                : {formatValue(c.before)} → {formatValue(c.after)}
              </Text>
            ))}
          </Stack>
        )}
      </Stack>
    </div>
  )
}

export function HistoryTab({ events, loading, error }: { events: AuditEventRead[]; loading: boolean; error: string | null }) {
  const { user } = useAuth()

  if (loading) return <Loader size="sm" />
  if (error) {
    return (
      <Text size="sm" c="red">
        {error}
      </Text>
    )
  }
  if (events.length === 0) {
    return (
      <Stack align="center" gap="xs" py="xl">
        <History size={24} color={slate[4]} />
        <Text size="sm" fw={600}>
          No history yet
        </Text>
        <Text size="sm" c="dimmed">
          Changes to this customer will appear here.
        </Text>
      </Stack>
    )
  }

  // Newest first — audit-log doesn't guarantee order server-side.
  const sorted = [...events].sort((a, b) => b.createdAt.localeCompare(a.createdAt))

  return (
    <div>
      {sorted.map((event) => (
        <HistoryEvent key={event.id} event={event} isYou={event.actorId === user?.id} />
      ))}
    </div>
  )
}
