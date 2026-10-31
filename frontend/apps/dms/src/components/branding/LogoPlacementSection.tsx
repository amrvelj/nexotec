import { useEffect, useState } from 'react'
import { Stack, Switch } from '@mantine/core'
import { useMutation } from '@tanstack/react-query'
import { LayoutTemplate } from 'lucide-react'
import { useTranslation } from 'react-i18next'
import { api } from '../../api/client'
import type { BrandingPlacements, BrandingRead } from '../../api/types'
import { useCanWrite } from '../../auth/useCanWrite'
import { apiRefusalText } from '../../utils/apiRefusal'
import { SectionCard } from '../business/SectionCard'

const PLACEMENTS: (keyof BrandingPlacements)[] = ['documentHeader', 'priceTag', 'listing', 'emailSignature', 'appHeader']

/**
 * KAN-205 — Logo and colours › Where the logo appears: one switch per
 * placement (PRD: "a dealer often wants the logo on a price tag but not in
 * the app chrome"). The document header is read by the renderer today; the
 * others are read by their screens as those ship.
 */
export function LogoPlacementSection({ branding, onWritten }: { branding: BrandingRead; onWritten: (data: BrandingRead) => void }) {
  const { t } = useTranslation()
  const canWrite = useCanWrite('branding')
  const [form, setForm] = useState<BrandingPlacements>(branding.placements)
  const [error, setError] = useState<string | null>(null)
  const [saved, setSaved] = useState(false)
  useEffect(() => setForm(branding.placements), [branding.placements])
  const changed = PLACEMENTS.filter((key) => form[key] !== branding.placements[key])

  const save = useMutation({
    mutationFn: () =>
      api.patch<BrandingRead>(
        `/dealerships/${branding.dealershipId}/branding`,
        { placements: Object.fromEntries(changed.map((key) => [key, form[key]])) },
        { 'If-Match': String(branding.version) },
      ),
    onSuccess: (data) => {
      setError(null)
      setSaved(true)
      onWritten(data)
    },
    onError: (err) => {
      setSaved(false)
      setError(apiRefusalText(t, err, 'branding.placements.failed'))
    },
  })

  return (
    <SectionCard
      title={t('branding.placements.title')}
      icon={<LayoutTemplate size={20} />}
      onSave={canWrite ? () => save.mutate() : undefined}
      dirty={changed.length > 0}
      saving={save.isPending}
      error={error}
      onDismissError={() => setError(null)}
      saved={saved}
    >
      <Stack gap="sm">
        {PLACEMENTS.map((key) => (
          <Switch
            key={key}
            label={t(`branding.placements.${key}`)}
            checked={form[key]}
            disabled={!canWrite}
            onChange={(e) => {
              const checked = e.currentTarget.checked
              setSaved(false)
              setForm((current) => ({ ...current, [key]: checked }))
            }}
          />
        ))}
      </Stack>
    </SectionCard>
  )
}

