import { useCallback, useId, useState } from 'react'
import { NumberInput, SimpleGrid, Stack, Switch, Text } from '@mantine/core'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { ShieldCheck } from 'lucide-react'
import { useTranslation } from 'react-i18next'
import { api } from '../../api/client'
import type { AuthConfigRead, AuthConfigUpdate } from '../../api/types'
import { businessRefusalText } from '../../businessOptions'
import { useFieldHint } from '../../hooks/useFieldHint'
import { authConfigKey } from './queryKeys'
import { SectionCard } from './SectionCard'
import { useRecordForm } from './useRecordForm'

interface Form {
  mfaEnforced: boolean
  // '' = no value of the Betrieb's own: the platform's applies.
  sessionIdleMinutes: number | string
  sessionAbsoluteHours: number | string
}

const toForm = (c: AuthConfigRead): Form => ({
  mfaEnforced: c.mfaEnforced,
  sessionIdleMinutes: c.sessionIdleMinutes ?? '',
  sessionAbsoluteHours: c.sessionAbsoluteHours ?? '',
})

const orNull = (v: number | string): number | null => (v === '' || v === null ? null : Number(v))

/**
 * Business › Security (KAN-229; FR-AU-11, D-A-37 / FR-AU-28; A-RULE-0): the
 * manager's switch for a second factor and the Betrieb's session policy —
 * stricter than the platform's, never looser (Anto, 2026-11-11: idle
 * 15–120 minutes, at most 1–12 hours, and never past midnight). An empty
 * field is the platform value. Saved through `PUT …/auth-config` with its
 * own If-Match. It applies to sessions signed in from now on.
 */
export function SecuritySection({ dealershipId }: { dealershipId: string }) {
  const config = useQuery({
    queryKey: authConfigKey(dealershipId),
    queryFn: () => api.get<AuthConfigRead>(`/dealerships/${dealershipId}/auth-config`),
    enabled: dealershipId !== '',
  })
  if (!config.data) return null
  return <SecurityForm dealershipId={dealershipId} config={config.data} />
}

function SecurityForm({ dealershipId, config }: { dealershipId: string; config: AuthConfigRead }) {
  const { t } = useTranslation()
  const hint = useFieldHint()
  const mfaHintId = useId()
  const queryClient = useQueryClient()
  const toFormCb = useCallback(toForm, [])
  const { form, set, dirty, markSaved, loaded } = useRecordForm(config, toFormCb)
  const [error, setError] = useState<string | null>(null)
  const [saved, setSaved] = useState(false)
  const bounds = loaded.sessionBounds
  const changed = JSON.stringify(form) !== JSON.stringify(toForm(loaded))

  const save = useMutation({
    mutationFn: async () => {
      const body: AuthConfigUpdate = {
        mfaEnforced: form.mfaEnforced,
        sessionIdleMinutes: orNull(form.sessionIdleMinutes),
        sessionAbsoluteHours: orNull(form.sessionAbsoluteHours),
      }
      const updated = await api.put<AuthConfigRead>(`/dealerships/${dealershipId}/auth-config`, body, {
        'If-Match': String(loaded.version),
      })
      markSaved(updated)
      queryClient.setQueryData(authConfigKey(dealershipId), updated)
    },
    onSuccess: () => {
      setError(null)
      setSaved(true)
    },
    onError: (err) => {
      setSaved(false)
      setError(businessRefusalText(t, err))
    },
  })

  return (
    <SectionCard
      title={t('business.security.title')}
      icon={<ShieldCheck size={20} />}
      onSave={() => save.mutate()}
      dirty={dirty && changed}
      saving={save.isPending}
      error={error}
      onDismissError={() => setError(null)}
      saved={saved}
    >
      <Stack gap="sm">
        <Switch
          {...hint(t('business.security.mfaEnforced'), t('business.security.mfaEnforcedHint'), { descriptionId: mfaHintId })}
          checked={form.mfaEnforced}
          onChange={(e) => set('mfaEnforced', e.currentTarget.checked)}
        />
        <SimpleGrid cols={{ base: 1, sm: 2 }}>
          <NumberInput
            {...hint(t('business.security.idleMinutes'), t('business.security.idleMinutesHint', { min: bounds.idleMinutesMin, max: bounds.idleMinutesMax }))}
            value={form.sessionIdleMinutes}
            onChange={(v) => set('sessionIdleMinutes', v)}
            min={bounds.idleMinutesMin}
            max={bounds.idleMinutesMax}
            allowDecimal={false}
            suffix={` ${t('business.security.minutes')}`}
            placeholder={t('business.security.idlePlaceholder', { minutes: bounds.platformIdleMinutes })}
          />
          <NumberInput
            {...hint(t('business.security.absoluteHours'), t('business.security.absoluteHoursHint', { min: bounds.absoluteHoursMin, max: bounds.absoluteHoursMax }))}
            value={form.sessionAbsoluteHours}
            onChange={(v) => set('sessionAbsoluteHours', v)}
            min={bounds.absoluteHoursMin}
            max={bounds.absoluteHoursMax}
            allowDecimal={false}
            suffix={` ${t('business.security.hours')}`}
            placeholder={t('business.security.absolutePlaceholder')}
          />
        </SimpleGrid>
        <Text c="dimmed" fz="sm" role="note">
          {t('business.security.appliesFromNextSignIn')}
        </Text>
      </Stack>
    </SectionCard>
  )
}
