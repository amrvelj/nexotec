import { Building2, TriangleAlert, User } from 'lucide-react'
import { Group, Stack, Text, UnstyledButton } from '@mantine/core'
import { useTranslation } from 'react-i18next'
import { Badge, LifecycleStatusBadge, purple, semantic, white } from '@nexotec/ui-kit'
import type { ContactDuplicateCandidate } from '../../../api/types'
import { translatedLifecycleLabel } from '../contactOptions'

function candidateName(c: ContactDuplicateCandidate): string {
  return c.contactType === 'business' ? (c.companyName ?? '') : `${c.firstName ?? ''} ${c.lastName ?? ''}`.trim()
}

// FR-04: "shows matches in a side panel with an 'open this contact
// instead' action. This never blocks creation." Renders inline within
// the wizard step rather than a true side-by-side panel — the create
// page's single-column layout has no room for one, and an appearing/
// disappearing block reads just as clearly as advisory, non-blocking UI.
export function DuplicateWarningPanel({
  candidates,
  onOpenExisting,
}: {
  candidates: ContactDuplicateCandidate[]
  onOpenExisting: (contactId: string) => void
}) {
  const { t } = useTranslation()
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
          {t('contactCreate.duplicates.heading', { count: candidates.length })}
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
              backgroundColor: white,
              border: '1px solid rgba(0,0,0,0.06)',
            }}
          >
            <Group gap={8} wrap="nowrap">
              {c.contactType === 'business' ? <Building2 size={16} /> : <User size={16} />}
              <Stack gap={0}>
                <Group gap={6}>
                  <Text size="sm" fw={600}>
                    {candidateName(c) || c.contactNumber}
                  </Text>
                  {c.match === 'exact' && <Badge tone="destructive">{t('contactCreate.duplicates.exactMatch')}</Badge>}
                </Group>
                <Text size="xs" c="dimmed">
                  {c.contactNumber}
                  {c.primaryPhone ? ` · ${c.primaryPhone}` : ''}
                  {c.primaryEmail ? ` · ${c.primaryEmail}` : ''}
                </Text>
              </Stack>
            </Group>
            <Group gap={8} wrap="nowrap">
              <LifecycleStatusBadge status={c.lifecycleStatus} label={translatedLifecycleLabel(t, c.lifecycleStatus)} />
              <Text size="xs" fw={600} c={purple[6]}>
                {t('contactCreate.duplicates.openInstead')}
              </Text>
            </Group>
          </UnstyledButton>
        ))}
      </Stack>
    </div>
  )
}
