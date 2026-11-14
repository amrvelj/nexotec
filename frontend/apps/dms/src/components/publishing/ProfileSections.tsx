import { useEffect, useId, useState } from 'react'
import { Alert, Anchor, NumberInput, Stack, Switch, Text, TextInput } from '@mantine/core'
import { useMutation } from '@tanstack/react-query'
import { Banknote, Globe, Scale, Tag } from 'lucide-react'
import { useTranslation } from 'react-i18next'
import { Link } from 'react-router-dom'
import { ChfInput } from '@nexotec/ui-kit'
import { api } from '../../api/client'
import type { LocationOverrideRead, PublishingProfileRead } from '../../api/types'
import { useFieldHint } from '../../hooks/useFieldHint'
import { apiRefusalText } from '../../utils/apiRefusal'
import { SectionCard } from '../business/SectionCard'
import { AgbDocumentField } from './AgbDocumentField'
import { LocalisedTextarea, type LocalisedText } from './LocalisedTextarea'

type Values = Record<string, unknown>

const IDENTITY_LOCALISED = ['shortDescription', 'longDescription'] as const
const LISTING_LOCALISED = ['warrantyText', 'mfkStatement', 'priceDisplayWording', 'standardEquipmentBlurb', 'tradeInPromotionText'] as const
const LEGAL_LOCALISED = ['impressum', 'privacyNotice', 'withdrawalWording'] as const
const FINANCING_MONEY = ['financingDownPaymentChf', 'financingResidualValueChf'] as const
const FINANCING_RATES = ['financingNominalRatePct', 'financingEffectiveRatePct'] as const
const SOCIAL_NETWORKS = ['facebook', 'instagram', 'linkedin', 'youtube', 'tiktok'] as const

const MAX_LENGTH: Record<string, number> = {
  shortDescription: 300,
  longDescription: 5000,
  impressum: 3000,
  privacyNotice: 20000,
  withdrawalWording: 5000,
  warrantyText: 2000,
  mfkStatement: 1000,
  priceDisplayWording: 120,
  standardEquipmentBlurb: 2000,
  tradeInPromotionText: 1000,
}

/** The fields of one section that differ from what was loaded — the PATCH body. */
function changed(fields: readonly string[], draft: Values, saved: Values): Values {
  const out: Values = {}
  for (const field of fields) {
    if (JSON.stringify(draft[field] ?? null) !== JSON.stringify(saved[field] ?? null)) out[field] = draft[field] ?? null
  }
  return out
}

function money(value: unknown): number | '' {
  return value === null || value === undefined || value === '' ? '' : Number(value)
}

/**
 * KAN-225 — one editable section of the publishing profile, for the Betrieb
 * (`target: 'betrieb'`) or for one location's override (`target: location
 * id`). Each section saves on its own with the screen's one version as
 * If-Match and hands the whole profile back.
 */
function useSectionSave(
  profile: PublishingProfileRead,
  target: string,
  fields: readonly string[],
  saved: Values,
  onWritten: (data: PublishingProfileRead) => void,
) {
  const { t } = useTranslation()
  const [draft, setDraft] = useState<Values>(saved)
  const [error, setError] = useState<string | null>(null)
  const [justSaved, setJustSaved] = useState(false)
  const savedKey = JSON.stringify(fields.map((f) => saved[f] ?? null))
  useEffect(() => {
    setDraft(saved)
    // Reset only when what was saved for these fields changes.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [savedKey, target])
  const body = changed(fields, draft, saved)
  const path =
    target === 'betrieb'
      ? `/dealerships/${profile.dealershipId}/publishing-profile`
      : `/dealerships/${profile.dealershipId}/publishing-profile/locations/${target}`
  const save = useMutation({
    mutationFn: () => api.patch<PublishingProfileRead>(path, body, { 'If-Match': String(profile.version) }),
    onSuccess: (data) => {
      setError(null)
      setJustSaved(true)
      onWritten(data)
    },
    onError: (err) => setError(apiRefusalText(t, err, 'publishing.saveFailed')),
  })
  const set = (field: string, value: unknown) => {
    setJustSaved(false)
    setDraft((current) => ({ ...current, [field]: value }))
  }
  return { draft, set, dirty: Object.keys(body).length > 0, save, error, setError, justSaved }
}

interface SectionProps {
  profile: PublishingProfileRead
  /** 'betrieb', or the location whose override this edits. */
  target: string
  override?: LocationOverrideRead
  canWrite: boolean
  onWritten: (data: PublishingProfileRead) => void
}

function savedValues(profile: PublishingProfileRead, override: LocationOverrideRead | undefined): Values {
  return (override ?? profile) as unknown as Values
}

export function IdentitySection({ profile, target, override, canWrite, onWritten }: SectionProps) {
  const { t } = useTranslation()
  const hint = useFieldHint()
  const publishedHintId = useId()
  const betrieb = target === 'betrieb'
  const fields = betrieb ? [...IDENTITY_LOCALISED, 'websiteDomain', 'isPublished', 'socialProfiles'] : [...IDENTITY_LOCALISED]
  const saved = savedValues(profile, override)
  const s = useSectionSave(profile, target, fields, saved, onWritten)
  const social = (s.draft.socialProfiles ?? {}) as Record<string, string>
  return (
    <SectionCard
      title={t('publishing.sections.identity')}
      icon={<Globe size={20} />}
      onSave={canWrite ? () => s.save.mutate() : undefined}
      dirty={s.dirty}
      saving={s.save.isPending}
      error={s.error}
      onDismissError={() => s.setError(null)}
      saved={s.justSaved}
    >
      <Stack gap="sm" id="publishing-identity">
        {betrieb && (
          <Text fz="sm">
            {t('publishing.identity.tradeName', { name: profile.tradeName ?? t('publishing.identity.noTradeName') })}{' '}
            <Anchor component={Link} to="/administration/business" fz="sm">
              {t('publishing.identity.editInBusiness')}
            </Anchor>
          </Text>
        )}
        {betrieb && (
          <TextInput
            {...hint(t('publishing.identity.websiteDomain'), t('publishing.identity.websiteDomainHint'))}
            value={(s.draft.websiteDomain as string | null) ?? ''}
            readOnly={!canWrite}
            onChange={(e) => s.set('websiteDomain', e.currentTarget.value || null)}
          />
        )}
        {betrieb && (
          <Switch
            {...hint(t('publishing.identity.isPublished'), t('publishing.identity.isPublishedHint'), { descriptionId: publishedHintId })}
            checked={Boolean(s.draft.isPublished)}
            disabled={!canWrite}
            onChange={(e) => s.set('isPublished', e.currentTarget.checked)}
          />
        )}
        {IDENTITY_LOCALISED.map((field) => (
          <LocalisedTextarea
            key={field}
            label={t(`publishing.fields.${field}`)}
            value={(s.draft[field] ?? {}) as LocalisedText}
            inherited={betrieb ? undefined : ((profile as unknown as Values)[field] as LocalisedText)}
            readOnly={!canWrite}
            maxLength={MAX_LENGTH[field]}
            minRows={field === 'longDescription' ? 4 : 2}
            onChange={(next) => s.set(field, next)}
          />
        ))}
        {betrieb &&
          SOCIAL_NETWORKS.map((network) => (
            <TextInput
              key={network}
              label={t(`publishing.social.${network}`)}
              placeholder="https://"
              value={social[network] ?? ''}
              readOnly={!canWrite}
              onChange={(e) => {
                const next = { ...social }
                if (e.currentTarget.value) next[network] = e.currentTarget.value
                else delete next[network]
                s.set('socialProfiles', next)
              }}
            />
          ))}
      </Stack>
    </SectionCard>
  )
}

export function LegalSection({ profile, canWrite, onWritten }: Omit<SectionProps, 'target' | 'override'>) {
  const { t } = useTranslation()
  const fields = [...LEGAL_LOCALISED, 'agbUrl']
  const s = useSectionSave(profile, 'betrieb', fields, profile as unknown as Values, onWritten)
  return (
    <SectionCard
      title={t('publishing.sections.legal')}
      icon={<Scale size={20} />}
      onSave={canWrite ? () => s.save.mutate() : undefined}
      dirty={s.dirty}
      saving={s.save.isPending}
      error={s.error}
      onDismissError={() => s.setError(null)}
      saved={s.justSaved}
    >
      <Stack gap="sm" id="publishing-legal">
        <Text c="dimmed" fz="sm">
          {t('publishing.legal.hint')}
        </Text>
        {LEGAL_LOCALISED.map((field) => (
          <LocalisedTextarea
            key={field}
            label={t(`publishing.fields.${field}`)}
            value={(s.draft[field] ?? {}) as LocalisedText}
            readOnly={!canWrite}
            maxLength={MAX_LENGTH[field]}
            minRows={3}
            onChange={(next) => s.set(field, next)}
          />
        ))}
        <TextInput
          label={t('publishing.fields.agbUrl')}
          placeholder="https://"
          value={(s.draft.agbUrl as string | null) ?? ''}
          readOnly={!canWrite}
          onChange={(e) => s.set('agbUrl', e.currentTarget.value || null)}
        />
        <AgbDocumentField profile={profile} canWrite={canWrite} onWritten={onWritten} />
      </Stack>
    </SectionCard>
  )
}

export function ListingDefaultsSection({ profile, target, override, canWrite, onWritten }: SectionProps) {
  const { t } = useTranslation()
  const hint = useFieldHint()
  const fields = [...LISTING_LOCALISED, 'deliveryFeeChf']
  const s = useSectionSave(profile, target, fields, savedValues(profile, override), onWritten)
  const inherit = target !== 'betrieb'
  return (
    <SectionCard
      title={t('publishing.sections.listingDefaults')}
      icon={<Tag size={20} />}
      onSave={canWrite ? () => s.save.mutate() : undefined}
      dirty={s.dirty}
      saving={s.save.isPending}
      error={s.error}
      onDismissError={() => s.setError(null)}
      saved={s.justSaved}
    >
      <Stack gap="sm" id="publishing-listingDefaults">
        {LISTING_LOCALISED.map((field) => (
          <LocalisedTextarea
            key={field}
            label={t(`publishing.fields.${field}`)}
            value={(s.draft[field] ?? {}) as LocalisedText}
            inherited={inherit ? ((profile as unknown as Values)[field] as LocalisedText) : undefined}
            readOnly={!canWrite}
            maxLength={MAX_LENGTH[field]}
            onChange={(next) => s.set(field, next)}
          />
        ))}
        <ChfInput
          {...hint(t('publishing.fields.deliveryFeeChf'), t('publishing.listing.deliveryFeeHint'))}
          value={money(s.draft.deliveryFeeChf)}
          readOnly={!canWrite}
          min={0}
          onChange={(v) => s.set('deliveryFeeChf', v === '' ? null : String(v))}
        />
      </Stack>
    </SectionCard>
  )
}

export function FinancingSection({ profile, target, override, canWrite, onWritten }: SectionProps) {
  const { t } = useTranslation()
  const fields = ['financingTermMonths', ...FINANCING_MONEY, ...FINANCING_RATES, 'financingProvider']
  const s = useSectionSave(profile, target, fields, savedValues(profile, override), onWritten)
  return (
    <SectionCard
      title={t('publishing.sections.financing')}
      icon={<Banknote size={20} />}
      onSave={canWrite ? () => s.save.mutate() : undefined}
      dirty={s.dirty}
      saving={s.save.isPending}
      error={s.error}
      onDismissError={() => s.setError(null)}
      saved={s.justSaved}
    >
      <Stack gap="sm" id="publishing-financing">
        <Alert color="yellow" variant="light" title={t('publishing.financing.pendingTitle')}>
          {t('publishing.financing.pendingLegalReview')}
        </Alert>
        <NumberInput
          label={t('publishing.fields.financingTermMonths')}
          value={money(s.draft.financingTermMonths)}
          readOnly={!canWrite}
          min={1}
          max={120}
          allowDecimal={false}
          onChange={(v) => s.set('financingTermMonths', v === '' ? null : Number(v))}
        />
        {FINANCING_MONEY.map((field) => (
          <ChfInput
            key={field}
            label={t(`publishing.fields.${field}`)}
            value={money(s.draft[field])}
            readOnly={!canWrite}
            min={0}
            onChange={(v) => s.set(field, v === '' ? null : String(v))}
          />
        ))}
        <NumberInput
          label={t('publishing.fields.financingNominalRatePct')}
          suffix=" %"
          decimalScale={3}
          value={money(s.draft.financingNominalRatePct)}
          readOnly={!canWrite}
          min={0}
          max={30}
          onChange={(v) => s.set('financingNominalRatePct', v === '' ? null : String(v))}
        />
        <NumberInput
          label={t('publishing.fields.financingEffectiveRatePct')}
          suffix=" %"
          decimalScale={3}
          value={money(s.draft.financingEffectiveRatePct)}
          readOnly={!canWrite}
          min={0}
          max={30}
          onChange={(v) => s.set('financingEffectiveRatePct', v === '' ? null : String(v))}
        />
        <TextInput
          label={t('publishing.fields.financingProvider')}
          value={(s.draft.financingProvider as string | null) ?? ''}
          readOnly={!canWrite}
          onChange={(e) => s.set('financingProvider', e.currentTarget.value || null)}
        />
      </Stack>
    </SectionCard>
  )
}
