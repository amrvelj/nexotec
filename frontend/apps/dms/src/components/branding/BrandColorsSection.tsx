import { useEffect, useState } from 'react'
import { Alert, Badge, ColorInput, Group, Stack, Text } from '@mantine/core'
import { useMutation } from '@tanstack/react-query'
import { Palette, Type } from 'lucide-react'
import { useTranslation } from 'react-i18next'
import { white } from '@nexotec/ui-kit'
import { api } from '../../api/client'
import type { BrandingRead, BrandingUpdate } from '../../api/types'
import { useCanWrite } from '../../auth/useCanWrite'
import { useFieldHint } from '../../hooks/useFieldHint'
import { apiRefusalText } from '../../utils/apiRefusal'
import { formatNumber } from '../../utils/format'
import { SectionCard } from '../business/SectionCard'
import { isHexColor, judgeBrandColors, type ContrastPair } from './contrast'

/**
 * KAN-205 — Logo and colours › Colours: the primary and the optional
 * secondary colour, with the contrast verdict (WCAG AA, 4.5:1) beside them
 * while the manager picks — the same formula the API judges the save with,
 * so what is shown is what the save returns. A failing pair warns; the save
 * goes through and says so (KAN-205: the PRD asks for the check, not for a
 * refusal — Anto's ruling on block vs. warn is pending).
 */
export function BrandColorsSection({ branding, onWritten }: { branding: BrandingRead; onWritten: (data: BrandingRead) => void }) {
  const { t } = useTranslation()
  const hint = useFieldHint()
  const canWrite = useCanWrite('branding')
  const [primary, setPrimary] = useState(branding.primaryColor)
  const [secondary, setSecondary] = useState(branding.secondaryColor ?? '')
  const [error, setError] = useState<string | null>(null)
  const [savedVerdict, setSavedVerdict] = useState<'passes' | 'fails' | null>(null)

  // A save elsewhere on the screen (a logo) moves the record on; the colours
  // follow the record whenever nothing here is being edited.
  useEffect(() => {
    setPrimary(branding.primaryColor)
    setSecondary(branding.secondaryColor ?? '')
  }, [branding.primaryColor, branding.secondaryColor])

  const secondaryOrNull = secondary.trim() === '' ? null : secondary
  const valid = isHexColor(primary) && (secondaryOrNull === null || isHexColor(secondaryOrNull))
  const dirty =
    primary.toUpperCase() !== branding.primaryColor.toUpperCase() ||
    (secondaryOrNull?.toUpperCase() ?? null) !== (branding.secondaryColor?.toUpperCase() ?? null)
  const pairs = judgeBrandColors(primary, secondaryOrNull)

  const save = useMutation({
    mutationFn: () => {
      const body: BrandingUpdate = {}
      if (primary.toUpperCase() !== branding.primaryColor.toUpperCase()) body.primaryColor = primary
      if ((secondaryOrNull?.toUpperCase() ?? null) !== (branding.secondaryColor?.toUpperCase() ?? null)) body.secondaryColor = secondaryOrNull
      return api.patch<BrandingRead>(`/dealerships/${branding.dealershipId}/branding`, body, { 'If-Match': String(branding.version) })
    },
    onSuccess: (data) => {
      setError(null)
      setSavedVerdict(data.contrast.passes ? 'passes' : 'fails')
      onWritten(data)
    },
    onError: (err) => {
      setSavedVerdict(null)
      setError(apiRefusalText(t, err, 'branding.colours.failed'))
    },
  })

  return (
    <SectionCard
      title={t('branding.colours.title')}
      icon={<Palette size={20} />}
      onSave={canWrite ? () => save.mutate() : undefined}
      dirty={dirty}
      saving={save.isPending}
      saveDisabled={!valid}
      saveDisabledReason={valid ? null : t('branding.colours.invalid')}
      error={error}
      onDismissError={() => setError(null)}
      saved={savedVerdict === 'passes'}
    >
      <Stack gap="md">
        <Group grow align="flex-start">
          <ColorInput
            {...hint(t('branding.colours.primary'), t('branding.colours.primaryHint'))}
            value={primary}
            onChange={(value) => {
              setPrimary(value)
              setSavedVerdict(null)
            }}
            format="hex"
            disabled={!canWrite}
            withEyeDropper={false}
          />
          <ColorInput
            {...hint(t('branding.colours.secondary'), t('branding.colours.secondaryHint'))}
            value={secondary}
            onChange={(value) => {
              setSecondary(value)
              setSavedVerdict(null)
            }}
            format="hex"
            disabled={!canWrite}
            withEyeDropper={false}
          />
        </Group>
        <Text c="dimmed" fz="sm">
          {t('branding.colours.explanation')}
        </Text>
        <Stack gap={6} role="list" aria-label={t('branding.colours.title')}>
          {pairs.map((pair) => (
            <ContrastRow key={pair.pair} pair={pair} />
          ))}
        </Stack>
        {savedVerdict === 'fails' && !dirty && (
          <Alert color="yellow" role="status">
            {t('branding.colours.savedWithWarning')}
          </Alert>
        )}
      </Stack>
    </SectionCard>
  )
}

function ContrastRow({ pair }: { pair: ContrastPair }) {
  const { t } = useTranslation()
  return (
    <Group gap="sm" role="listitem" wrap="nowrap">
      <span
        aria-hidden
        style={{
          display: 'inline-flex',
          alignItems: 'center',
          justifyContent: 'center',
          width: 40,
          height: 24,
          borderRadius: 'var(--mantine-radius-sm)',
          border: '1px solid var(--mantine-color-gray-3)',
          background: pair.background ?? white,
          color: pair.foreground,
          fontWeight: 700,
          fontSize: 12,
        }}
      >
        <Type size={14} />
      </span>
      <Text fz="sm" style={{ flex: 1 }}>
        {t(`branding.colours.pairs.${pair.pair}`)}
      </Text>
      <Text fz="sm" ff="monospace">
        {t('branding.colours.ratio', { ratio: formatNumber(pair.ratio, 2) })}
      </Text>
      <Badge color={pair.passes ? 'green' : 'red'} variant="light">
        {pair.passes ? t('branding.colours.passes') : t('branding.colours.fails')}
      </Badge>
    </Group>
  )
}
