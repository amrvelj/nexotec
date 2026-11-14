import { useEffect, useState } from 'react'
import { Select, Stack, Switch, Text, TextInput } from '@mantine/core'
import { useMutation } from '@tanstack/react-query'
import { Inbox } from 'lucide-react'
import { useTranslation } from 'react-i18next'
import { api } from '../../api/client'
import type { LeadRoutingRead, LeadRoutingUpdate, PublishingProfileRead } from '../../api/types'
import { useFieldHint } from '../../hooks/useFieldHint'
import { apiRefusalText } from '../../utils/apiRefusal'
import { SectionCard } from '../business/SectionCard'

const RECIPIENT_ROLES = ['sales', 'aftersales', 'parts', 'inventory', 'finance'] as const

/**
 * KAN-225 — a site's lead routing (PRD Publishing profile, Lead routing):
 * where enquiries go, whether they also open a task, and which role
 * receives them. The lead e-mail is the location's own field (Locations);
 * saved here it moves the location's record too.
 */
export function LeadRoutingSection({
  profile,
  routing,
  canWrite,
  onWritten,
}: {
  profile: PublishingProfileRead
  routing: LeadRoutingRead
  canWrite: boolean
  onWritten: (data: PublishingProfileRead) => void
}) {
  const { t } = useTranslation()
  const hint = useFieldHint()
  const initial: LeadRoutingUpdate = {
    leadEmail: routing.leadEmail,
    leadPhone: routing.leadPhone,
    createsTask: routing.createsTask,
    recipientRole: routing.recipientRole,
    recipientUserId: routing.recipientUserId,
  }
  const [draft, setDraft] = useState<LeadRoutingUpdate>(initial)
  const [error, setError] = useState<string | null>(null)
  const [saved, setSaved] = useState(false)
  const initialKey = JSON.stringify(initial)
  useEffect(() => {
    setDraft(JSON.parse(initialKey) as LeadRoutingUpdate)
  }, [initialKey])
  const dirty = JSON.stringify(draft) !== initialKey
  const save = useMutation({
    mutationFn: () =>
      api.put<PublishingProfileRead>(
        `/dealerships/${profile.dealershipId}/publishing-profile/locations/${routing.locationId}/lead-routing`,
        draft,
        { 'If-Match': String(profile.version) },
      ),
    onSuccess: (data) => {
      setError(null)
      setSaved(true)
      onWritten(data)
    },
    onError: (err) => setError(apiRefusalText(t, err, 'publishing.saveFailed')),
  })
  const set = (patch: Partial<LeadRoutingUpdate>) => {
    setSaved(false)
    setDraft((current) => ({ ...current, ...patch }))
  }
  return (
    <SectionCard
      title={t('publishing.sections.leadRouting')}
      icon={<Inbox size={20} />}
      onSave={canWrite ? () => save.mutate() : undefined}
      dirty={dirty}
      saving={save.isPending}
      error={error}
      onDismissError={() => setError(null)}
      saved={saved}
    >
      <Stack gap="sm" id="publishing-leadRouting">
        <Text c="dimmed" fz="sm">
          {t('publishing.leadRouting.hint')}
        </Text>
        <TextInput
          label={t('publishing.leadRouting.leadEmail')}
          type="email"
          value={draft.leadEmail ?? ''}
          readOnly={!canWrite}
          onChange={(e) => set({ leadEmail: e.currentTarget.value || null })}
        />
        <TextInput
          label={t('publishing.leadRouting.leadPhone')}
          value={draft.leadPhone ?? ''}
          readOnly={!canWrite}
          onChange={(e) => set({ leadPhone: e.currentTarget.value || null })}
        />
        <Switch
          label={t('publishing.leadRouting.createsTask')}
          checked={Boolean(draft.createsTask)}
          disabled={!canWrite}
          onChange={(e) => set({ createsTask: e.currentTarget.checked })}
        />
        <Select
          {...hint(t('publishing.leadRouting.recipientRole'), t('publishing.leadRouting.recipientRoleHint'))}
          data={RECIPIENT_ROLES.map((role) => ({ value: role, label: t(`usersAdmin.accessRole.${role}`) }))}
          value={draft.recipientRole ?? null}
          clearable
          readOnly={!canWrite}
          onChange={(value) => set({ recipientRole: (value as LeadRoutingUpdate['recipientRole']) ?? null, recipientUserId: null })}
        />
      </Stack>
    </SectionCard>
  )
}
