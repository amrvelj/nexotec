import { Building2, TriangleAlert, User } from 'lucide-react'
import { Group, Stack, Text, UnstyledButton } from '@mantine/core'
import { Badge, LifecycleStatusBadge, purple, semantic } from '@nexotec/ui-kit'
import type { CustomerDuplicateCandidate } from '../api/types'

function candidateName(c: CustomerDuplicateCandidate): string {
  return c.customerType === 'business' ? (c.companyName ?? '') : `${c.firstName ?? ''} ${c.lastName ?? ''}`.trim()
}

// FR-04: "shows matches in a side panel with an 'open this customer
// instead' action. This never blocks creation." Renders inline within
// the wizard step rather than a true side-by-side panel — the create
// page's single-column layout has no room for one, and an appearing/
// disappearing block reads just as clearly as advisory, non-blocking UI.
export function DuplicateWarningPanel({
  candidates,
  onOpenExisting,
}: {
  candidates: CustomerDuplicateCandidate[]
  onOpenExisting: (customerId: string) => void
}) {
  if (candidates.length === 0) return null

  return (
    <div
      style={{
        border: `1px solid ${semantic.warning.border}`,
        backgroundColor: semantic.warning.surface,
        borderRadius: 10,
        padding: 12,
      }}
    >
      <Group gap={6} mb={8}>
        <TriangleAlert size={16} color={semantic.warning.text} />
        <Text size="sm" fw={600} c={semantic.warning.text}>
          {candidates.length === 1 ? 'Possible existing customer' : `${candidates.length} possible existing customers`}
        </Text>
      </Group>
      <Stack gap={6}>
        {candidates.map((c) => (
          <UnstyledButton
            key={c.id}
            onClick={() => onOpenExisting(c.id)}
            style={{
              display: 'flex',
              alignItems: 'center',
              justifyContent: 'space-between',
              gap: 10,
              padding: '8px 10px',
              borderRadius: 8,
              backgroundColor: '#fff',
              border: '1px solid rgba(0,0,0,0.06)',
            }}
          >
            <Group gap={8} wrap="nowrap">
              {c.customerType === 'business' ? <Building2 size={16} /> : <User size={16} />}
              <Stack gap={0}>
                <Group gap={6}>
                  <Text size="sm" fw={600}>
                    {candidateName(c) || c.customerNumber}
                  </Text>
                  {c.match === 'exact' && <Badge tone="destructive">Exact match</Badge>}
                </Group>
                <Text size="xs" c="dimmed">
                  {c.customerNumber}
                  {c.primaryPhone ? ` · ${c.primaryPhone}` : ''}
                  {c.primaryEmail ? ` · ${c.primaryEmail}` : ''}
                </Text>
              </Stack>
            </Group>
            <Group gap={8} wrap="nowrap">
              <LifecycleStatusBadge status={c.lifecycleStatus} />
              <Text size="xs" fw={600} c={purple[6]}>
                Open instead
              </Text>
            </Group>
          </UnstyledButton>
        ))}
      </Stack>
    </div>
  )
}
