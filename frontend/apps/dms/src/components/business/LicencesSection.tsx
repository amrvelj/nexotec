import { useCallback } from 'react'
import { ActionIcon, Button, Group, MultiSelect, Select, SimpleGrid, Stack, Text, TextInput } from '@mantine/core'
import { Award, Plus, Trash2 } from 'lucide-react'
import { useTranslation } from 'react-i18next'
import type { DealershipCollectiveVehicleLicence, DealershipRead, DealershipUpdate } from '../../api/types'
import { orNull } from '../../businessOptions'
import { CANTON_OPTIONS } from '../../customerOptions'
import { SectionCard } from './SectionCard'
import { useDealershipPatch } from './useDealershipPatch'
import { useListOptions } from './useListOptions'
import { useRecordForm } from './useRecordForm'

interface LicenceRow {
  plateNumber: string
  canton: string | null
  expiresOn: string
}

interface Form {
  licences: LicenceRow[]
  astraOperatorNumber: string
  associationMemberships: string[]
}

const toForm = (d: DealershipRead): Form => ({
  licences: (d.collectiveVehicleLicences ?? []).map((l) => ({ plateNumber: l.plateNumber, canton: l.canton, expiresOn: l.expiresOn ?? '' })),
  astraOperatorNumber: d.astraOperatorNumber ?? '',
  associationMemberships: d.associationMemberships ?? [],
})

function licencesOf(rows: LicenceRow[]): DealershipCollectiveVehicleLicence[] {
  return rows
    .filter((r) => r.plateNumber.trim() !== '')
    .map((r) => ({ plateNumber: r.plateNumber.trim(), canton: r.canton ?? '', expiresOn: orNull(r.expiresOn) }))
}

function changesOf(d: DealershipRead, f: Form): DealershipUpdate {
  const c: DealershipUpdate = {}
  const licences = licencesOf(f.licences)
  const before = (d.collectiveVehicleLicences ?? []).map((l) => ({ plateNumber: l.plateNumber, canton: l.canton, expiresOn: l.expiresOn ?? null }))
  if (JSON.stringify(licences) !== JSON.stringify(before)) c.collectiveVehicleLicences = licences.length ? licences : null
  if (orNull(f.astraOperatorNumber) !== (d.astraOperatorNumber ?? null)) c.astraOperatorNumber = orNull(f.astraOperatorNumber)
  if (JSON.stringify(f.associationMemberships) !== JSON.stringify(d.associationMemberships ?? [])) c.associationMemberships = f.associationMemberships.length ? f.associationMemberships : null
  return c
}

/**
 * Business › Licences and memberships (KAN-203; D-A-27): the Händlerschild
 * numbers with issuing canton and expiry, the ASTRA/MOFIS operator number
 * (the prerequisite for the registry integration) and the association
 * memberships that print in footers.
 */
export function LicencesSection({ dealership }: { dealership: DealershipRead }) {
  const { t } = useTranslation()
  const { form, set, dirty, markSaved, loaded } = useRecordForm(dealership, useCallback(toForm, []))
  const patch = useDealershipPatch(dealership.id)
  const associations = useListOptions('association_membership')
  const changes = changesOf(loaded, form)
  const incomplete = form.licences.some((r) => r.plateNumber.trim() !== '' && !r.canton)

  const setRow = (index: number, row: LicenceRow) => set('licences', form.licences.map((r, i) => (i === index ? row : r)))

  return (
    <SectionCard
      title={t('business.licences.title')}
      icon={<Award size={20} />}
      onSave={() => patch.save({ changes, version: loaded.version }, { onSuccess: markSaved })}
      dirty={dirty && Object.keys(changes).length > 0}
      saving={patch.saving}
      saveDisabled={incomplete}
      saveDisabledReason={incomplete ? t('business.licences.cantonRequired') : null}
      error={patch.error}
      onDismissError={patch.clearError}
      saved={patch.saved}
    >
      <Stack gap="md">
        <div>
          <Text fw={600} fz="sm">
            {t('business.licences.haendlerschild')}
          </Text>
          <Text c="dimmed" fz="sm">
            {t('business.licences.haendlerschildHint')}
          </Text>
        </div>
        {form.licences.map((row, index) => (
          <Group key={index} align="flex-end" gap="sm" wrap="nowrap">
            <TextInput label={t('business.licences.plate')} value={row.plateNumber} onChange={(e) => setRow(index, { ...row, plateNumber: e.currentTarget.value.toUpperCase() })} placeholder="ZH 12345 U" style={{ flex: 1 }} />
            <Select label={t('business.licences.canton')} data={CANTON_OPTIONS} value={row.canton} onChange={(v) => setRow(index, { ...row, canton: v })} searchable style={{ flex: 1 }} />
            <TextInput label={t('business.licences.expiresOn')} type="date" value={row.expiresOn} onChange={(e) => setRow(index, { ...row, expiresOn: e.currentTarget.value })} style={{ flex: 1 }} />
            <ActionIcon variant="subtle" color="red" aria-label={t('business.licences.remove')} onClick={() => set('licences', form.licences.filter((_, i) => i !== index))}>
              <Trash2 size={16} />
            </ActionIcon>
          </Group>
        ))}
        <Group>
          <Button variant="default" size="xs" leftSection={<Plus size={14} />} onClick={() => set('licences', [...form.licences, { plateNumber: '', canton: null, expiresOn: '' }])}>
            {t('business.licences.add')}
          </Button>
        </Group>
        <SimpleGrid cols={{ base: 1, sm: 2 }}>
          <TextInput label={t('business.licences.astra')} value={form.astraOperatorNumber} onChange={(e) => set('astraOperatorNumber', e.currentTarget.value)} description={t('business.licences.astraHint')} />
          <MultiSelect label={t('business.licences.associations')} data={associations.options} value={form.associationMemberships} onChange={(v) => set('associationMemberships', v)} searchable clearable description={t('business.licences.associationsHint')} />
        </SimpleGrid>
      </Stack>
    </SectionCard>
  )
}
