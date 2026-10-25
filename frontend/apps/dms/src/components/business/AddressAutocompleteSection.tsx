import { useCallback, useId, useState } from 'react'
import { Stack, Switch, Text } from '@mantine/core'
import { useMutation, useQueryClient } from '@tanstack/react-query'
import { MapPinned } from 'lucide-react'
import { useTranslation } from 'react-i18next'
import { api } from '../../api/client'
import type { DealershipSettingsRead, DealershipSettingsUpdate } from '../../api/types'
import { businessRefusalText } from '../../businessOptions'
import { addressAutocompleteConfigKey, useAddressAutocompleteConfig } from '../../hooks/useAddressAutocompleteConfig'
import { useFieldHint } from '../../hooks/useFieldHint'
import { settingsKey } from './queryKeys'
import { SectionCard } from './SectionCard'
import { useRecordForm } from './useRecordForm'

interface Form {
  addressAutocompleteEnabled: boolean
}

/**
 * Business › Address entry (KAN-37, ADR-101; A-RULE-0): the Betrieb's switch
 * for Google Places Autocomplete on every address form — on by default, the
 * manager turns it off (Anto, 2026-10-25). It is a `dealership_settings`
 * field, saved with that record's If-Match like the fiscal defaults; the
 * key behind it is the platform's, so a Betrieb whose switch is on while
 * Nexotec holds no key is told so beneath the switch.
 */
export function AddressAutocompleteSection({ settings }: { settings: DealershipSettingsRead }) {
  const { t } = useTranslation()
  const hint = useFieldHint()
  const hintId = useId()
  const queryClient = useQueryClient()
  const config = useAddressAutocompleteConfig()
  const toForm = useCallback((s: DealershipSettingsRead): Form => ({ addressAutocompleteEnabled: s.addressAutocompleteEnabled }), [])
  const { form, set, dirty, markSaved, loaded } = useRecordForm(settings, toForm)
  const [error, setError] = useState<string | null>(null)
  const [saved, setSaved] = useState(false)
  const changed = form.addressAutocompleteEnabled !== loaded.addressAutocompleteEnabled

  const save = useMutation({
    mutationFn: async () => {
      const changes: DealershipSettingsUpdate = { addressAutocompleteEnabled: form.addressAutocompleteEnabled }
      const updated = await api.patch<DealershipSettingsRead>(`/dealerships/${settings.dealershipId}/settings`, changes, {
        'If-Match': String(loaded.version),
      })
      markSaved(updated)
      queryClient.setQueryData(settingsKey(settings.dealershipId), updated)
      // Every open address form reads the switch through this key.
      await queryClient.invalidateQueries({ queryKey: addressAutocompleteConfigKey(settings.dealershipId) })
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
      title={t('business.addressEntry.title')}
      icon={<MapPinned size={20} />}
      onSave={() => save.mutate()}
      dirty={dirty && changed}
      saving={save.isPending}
      error={error}
      onDismissError={() => setError(null)}
      saved={saved}
    >
      <Stack gap="sm">
        <Switch
          {...hint(t('business.addressEntry.enabled'), t('business.addressEntry.enabledHint'), { descriptionId: hintId })}
          checked={form.addressAutocompleteEnabled}
          onChange={(e) => set('addressAutocompleteEnabled', e.currentTarget.checked)}
        />
        {loaded.addressAutocompleteEnabled && !config.enabled && (
          <Text c="dimmed" fz="sm" role="note">
            {t('business.addressEntry.unavailable')}
          </Text>
        )}
      </Stack>
    </SectionCard>
  )
}
