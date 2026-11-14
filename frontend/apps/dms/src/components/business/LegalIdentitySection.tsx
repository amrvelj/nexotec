import { useCallback, useState } from 'react'
import { useFieldHint } from '../../hooks/useFieldHint'
import { Checkbox, MultiSelect, Select, SimpleGrid, Stack, Switch, Text, TextInput } from '@mantine/core'
import { Landmark } from 'lucide-react'
import { useTranslation } from 'react-i18next'
import type { DealershipRead, DealershipUpdate, FranchiseType } from '../../api/types'
import { FRANCHISE_TYPES, orNull } from '../../businessOptions'
import { CANTON_OPTIONS } from '../../shared/cantons'
import { SectionCard } from './SectionCard'
import { useDealershipPatch } from './useDealershipPatch'
import { useListOptions } from './useListOptions'
import { useRecordForm } from './useRecordForm'

interface LegalForm {
  legalName: string
  tradeName: string
  legalForm: string | null
  franchiseType: FranchiseType
  oemAffiliations: string[]
  uid: string
  vatNumber: string
  vatLiable: boolean
  commercialRegisterOffice: string | null
  hrNumber: string
}

const toForm = (d: DealershipRead): LegalForm => ({
  legalName: d.legalName,
  tradeName: d.tradeName ?? '',
  legalForm: d.legalForm ?? null,
  franchiseType: d.franchiseType,
  oemAffiliations: d.oemAffiliations ?? [],
  uid: d.uid ?? '',
  vatNumber: d.vatNumber ?? '',
  vatLiable: d.vatLiable,
  commercialRegisterOffice: d.commercialRegisterOffice ?? null,
  hrNumber: d.hrNumber ?? '',
})

/** Only what changed, as PATCH semantics want. */
function changesOf(d: DealershipRead, f: LegalForm): DealershipUpdate {
  const c: DealershipUpdate = {}
  if (f.legalName.trim() !== d.legalName) c.legalName = f.legalName.trim()
  if (orNull(f.tradeName) !== (d.tradeName ?? null)) c.tradeName = orNull(f.tradeName)
  if (f.legalForm !== (d.legalForm ?? null)) c.legalForm = f.legalForm
  if (f.franchiseType !== d.franchiseType) c.franchiseType = f.franchiseType
  if (JSON.stringify(f.oemAffiliations) !== JSON.stringify(d.oemAffiliations ?? [])) c.oemAffiliations = f.oemAffiliations.length ? f.oemAffiliations : null
  if (f.uid.trim() !== (d.uid ?? '') && f.uid.trim() !== '') c.uid = f.uid.trim()
  if (orNull(f.vatNumber) !== (d.vatNumber ?? null)) c.vatNumber = orNull(f.vatNumber)
  if (f.vatLiable !== d.vatLiable) c.vatLiable = f.vatLiable
  if (f.commercialRegisterOffice !== (d.commercialRegisterOffice ?? null)) c.commercialRegisterOffice = f.commercialRegisterOffice
  if (orNull(f.hrNumber) !== (d.hrNumber ?? null)) c.hrNumber = orNull(f.hrNumber)
  return c
}

const LEGAL_IDENTITY_KEYS: (keyof DealershipUpdate)[] = ['legalName', 'uid', 'vatNumber', 'vatLiable']

/**
 * Business › Legal identity (KAN-203; PRD "Dealership master data" ›
 * Legal identity). A change of legal name, UID or VAT is saved only against
 * the manager's confirmation (the matrix row; Anto 2026-10-24: a
 * confirmation, no mandatory note), with an optional note kept in the audit
 * trail.
 */
export function LegalIdentitySection({ dealership }: { dealership: DealershipRead }) {
  const { t } = useTranslation()
  const hint = useFieldHint()
  const { form, set, dirty, markSaved, loaded } = useRecordForm(dealership, useCallback(toForm, []))
  const [confirmed, setConfirmed] = useState(false)
  const [auditNote, setAuditNote] = useState('')
  const patch = useDealershipPatch(dealership.id)
  const legalForms = useListOptions('legal_form')
  const oems = useListOptions('oem_affiliations')

  const changes = changesOf(loaded, form)
  const touchesIdentity = LEGAL_IDENTITY_KEYS.some((key) => key in changes)
  const hasChanges = Object.keys(changes).length > 0

  const save = () => {
    const body: DealershipUpdate = { ...changes }
    if (touchesIdentity) {
      body.confirmation = true
      if (auditNote.trim()) body.auditNote = auditNote.trim()
    }
    patch.save({ changes: body, version: loaded.version }, {
      onSuccess: (updated) => {
        markSaved(updated)
        setConfirmed(false)
        setAuditNote('')
      },
    })
  }

  return (
    <SectionCard
      title={t('business.legal.title')}
      icon={<Landmark size={20} />}
      onSave={save}
      dirty={dirty && hasChanges}
      saving={patch.saving}
      saveDisabled={touchesIdentity && !confirmed}
      saveDisabledReason={touchesIdentity && !confirmed ? t('business.legal.confirmFirst') : null}
      error={patch.error}
      onDismissError={patch.clearError}
      saved={patch.saved}
    >
      <Stack gap="md">
        <SimpleGrid cols={{ base: 1, sm: 2 }}>
          <TextInput {...hint(t('business.legal.legalName'), t('business.legal.legalNameHint'))} value={form.legalName} onChange={(e) => set('legalName', e.currentTarget.value)} required />
          <TextInput {...hint(t('business.legal.tradeName'), t('business.legal.tradeNameHint'))} value={form.tradeName} onChange={(e) => set('tradeName', e.currentTarget.value)} />
          <Select label={t('business.legal.legalForm')} data={legalForms.options} value={form.legalForm} onChange={(v) => set('legalForm', v)} clearable searchable placeholder={legalForms.isLoading ? t('common.loading') : undefined} />
          <Select
            label={t('business.legal.franchiseType')}
            data={FRANCHISE_TYPES.map((v) => ({ value: v, label: t(`business.legal.franchiseTypes.${v}`) }))}
            value={form.franchiseType}
            onChange={(v) => v && set('franchiseType', v as FranchiseType)}
            allowDeselect={false}
          />
        </SimpleGrid>
        <MultiSelect label={t('business.legal.oemAffiliations')} data={oems.options} value={form.oemAffiliations} onChange={(v) => set('oemAffiliations', v)} searchable clearable />
        <SimpleGrid cols={{ base: 1, sm: 2 }}>
          <TextInput {...hint(t('business.legal.uid'), t('business.legal.uidHint'))} value={form.uid} onChange={(e) => set('uid', e.currentTarget.value)} placeholder="CHE-123.456.789" required />
          <TextInput label={t('business.legal.vatNumber')} value={form.vatNumber} onChange={(e) => set('vatNumber', e.currentTarget.value)} placeholder="CHE-123.456.789 MWST" />
          <Select label={t('business.legal.commercialRegisterOffice')} data={CANTON_OPTIONS} value={form.commercialRegisterOffice} onChange={(v) => set('commercialRegisterOffice', v)} clearable searchable />
          <TextInput {...hint(t('business.legal.hrNumber'), t('business.legal.hrNumberHint'))} value={form.hrNumber} onChange={(e) => set('hrNumber', e.currentTarget.value)} placeholder="CH-020.3.012.345-6" />
        </SimpleGrid>
        <Switch label={t('business.legal.vatLiable')} checked={form.vatLiable} onChange={(e) => set('vatLiable', e.currentTarget.checked)} />
        {touchesIdentity && (
          <Stack gap="xs" role="group" aria-label={t('business.legal.confirmTitle')}>
            <Text fw={600}>{t('business.legal.confirmTitle')}</Text>
            <Text c="dimmed" fz="sm">
              {t('business.legal.confirmHint')}
            </Text>
            <Checkbox label={t('business.legal.confirmLabel')} checked={confirmed} onChange={(e) => setConfirmed(e.currentTarget.checked)} />
            <TextInput label={t('business.legal.auditNote')} value={auditNote} onChange={(e) => setAuditNote(e.currentTarget.value)} placeholder={t('business.legal.auditNotePlaceholder')} maxLength={500} />
          </Stack>
        )}
      </Stack>
    </SectionCard>
  )
}
