import { useCallback, type ReactNode } from 'react'
import { Select, SimpleGrid, Stack, Switch, TextInput } from '@mantine/core'
import { MapPin } from 'lucide-react'
import { ProgressiveDisclosure } from '@nexotec/ui-kit'
import { useFieldHint } from '../../hooks/useFieldHint'
import { useTranslation } from 'react-i18next'
import type { DealershipAddress, DealershipOverrideAddress, DealershipRead, DealershipUpdate } from '../../api/types'
import { CANTON_OPTIONS } from '../../customerOptions'
import { AddressSearchField } from '../AddressSearchField'
import { SectionCard } from './SectionCard'
import { useDealershipPatch } from './useDealershipPatch'
import { useRecordForm } from './useRecordForm'

interface AddressForm {
  street: string
  houseNumber: string
  postalCode: string
  locality: string
  canton: string | null
  country: string
}

interface Form {
  seat: AddressForm
  phone: string
  correspondenceDiffers: boolean
  correspondence: AddressForm
  billingDiffers: boolean
  billing: AddressForm
}

const EMPTY: AddressForm = { street: '', houseNumber: '', postalCode: '', locality: '', canton: null, country: 'CH' }

const addressForm = (a: DealershipAddress | DealershipOverrideAddress | null | undefined): AddressForm =>
  a ? { street: a.street, houseNumber: a.houseNumber, postalCode: a.postalCode, locality: a.locality, canton: a.canton ?? null, country: a.country ?? 'CH' } : EMPTY

const toForm = (d: DealershipRead): Form => ({
  seat: addressForm(d.address),
  phone: d.phone,
  correspondenceDiffers: d.correspondenceAddress != null,
  correspondence: addressForm(d.correspondenceAddress ?? d.address),
  billingDiffers: d.billingAddress != null,
  billing: addressForm(d.billingAddress ?? d.address),
})

function toOverride(f: AddressForm): DealershipOverrideAddress {
  const country = f.country.trim().toUpperCase() || 'CH'
  return {
    street: f.street.trim(),
    houseNumber: f.houseNumber.trim(),
    postalCode: f.postalCode.trim(),
    locality: f.locality.trim(),
    canton: country === 'CH' ? f.canton : null,
    country,
  }
}

const same = (a: unknown, b: unknown) => JSON.stringify(a) === JSON.stringify(b)

function changesOf(d: DealershipRead, f: Form): DealershipUpdate {
  const c: DealershipUpdate = {}
  const seat: DealershipAddress = { ...toOverride(f.seat), canton: f.seat.canton ?? '' }
  if (!same(seat, d.address)) c.address = seat
  if (f.phone.trim() !== d.phone) c.phone = f.phone.trim()
  const correspondence = f.correspondenceDiffers ? toOverride(f.correspondence) : null
  if (!same(correspondence, d.correspondenceAddress ?? null)) c.correspondenceAddress = correspondence
  const billing = f.billingDiffers ? toOverride(f.billing) : null
  if (!same(billing, d.billingAddress ?? null)) c.billingAddress = billing
  return c
}

function AddressFields({ value, onChange, idPrefix, seat, extra }: { value: AddressForm; onChange: (v: AddressForm) => void; idPrefix: string; seat?: boolean; extra?: ReactNode }) {
  const { t } = useTranslation()
  const hint = useFieldHint()
  const set = <K extends keyof AddressForm>(key: K, v: AddressForm[K]) => onChange({ ...value, [key]: v })
  const swiss = (value.country || 'CH').toUpperCase() === 'CH'
  return (
    <Stack gap="sm">
      {/* KAN-37: a Google Places pick fills street, number, postal code, locality and country. The canton
          is never Google's (D-13's rule): a pick that moves the postal code clears it, and the required
          Select makes the manager pick it again. */}
      <AddressSearchField
        id={`${idPrefix}-search`}
        country={value.country || 'CH'}
        onSelect={(address) =>
          onChange({
            ...value,
            street: address.street,
            houseNumber: address.houseNumber,
            postalCode: address.postalCode,
            locality: address.locality,
            country: seat ? value.country : address.country || value.country,
            canton: address.postalCode === value.postalCode ? value.canton : null,
          })
        }
      />
      <SimpleGrid cols={{ base: 1, sm: 3 }}>
        <TextInput id={`${idPrefix}-street`} label={t('business.addresses.street')} value={value.street} onChange={(e) => set('street', e.currentTarget.value)} required />
        <TextInput id={`${idPrefix}-house`} label={t('business.addresses.houseNumber')} value={value.houseNumber} onChange={(e) => set('houseNumber', e.currentTarget.value)} required />
        <TextInput id={`${idPrefix}-postal`} label={t('business.addresses.postalCode')} value={value.postalCode} onChange={(e) => set('postalCode', e.currentTarget.value)} required />
        <TextInput id={`${idPrefix}-locality`} label={t('business.addresses.locality')} value={value.locality} onChange={(e) => set('locality', e.currentTarget.value)} required />
        {swiss && <Select id={`${idPrefix}-canton`} label={t('business.addresses.canton')} data={CANTON_OPTIONS} value={value.canton} onChange={(v) => set('canton', v)} searchable required />}
        {extra}
        {!seat && <TextInput id={`${idPrefix}-country`} {...hint(t('business.addresses.country'), t('business.addresses.countryHint'))} value={value.country} onChange={(e) => set('country', e.currentTarget.value.toUpperCase())} maxLength={2} />}
      </SimpleGrid>
    </Stack>
  )
}

/**
 * Business › Addresses (KAN-203, D-A-25): the registered seat from the
 * Handelsregister, and the two overrides — where post goes, where Nexotec's
 * own invoices go — collapsed behind "differs from the registered seat",
 * because for most garages all three are one building.
 */
export function AddressesSection({ dealership }: { dealership: DealershipRead }) {
  const { t } = useTranslation()
  const { form, set, dirty, markSaved, loaded } = useRecordForm(dealership, useCallback(toForm, []))
  const patch = useDealershipPatch(dealership.id)
  const changes = changesOf(loaded, form)

  return (
    <SectionCard
      title={t('business.addresses.title')}
      icon={<MapPin size={20} />}
      onSave={() => patch.save({ changes, version: loaded.version }, { onSuccess: (updated) => markSaved(updated) })}
      dirty={dirty && Object.keys(changes).length > 0}
      saving={patch.saving}
      error={patch.error}
      onDismissError={patch.clearError}
      saved={patch.saved}
    >
      <Stack gap="md">
        <AddressFields
          idPrefix="seat"
          value={form.seat}
          onChange={(v) => set('seat', v)}
          seat
          extra={<TextInput label={t('business.addresses.phone')} value={form.phone} onChange={(e) => set('phone', e.currentTarget.value)} placeholder="+41 44 …" required />}
        />
        <ProgressiveDisclosure label={t('business.addresses.overrides')}>
          <Stack gap="md">
            <Switch label={t('business.addresses.correspondenceDiffers')} checked={form.correspondenceDiffers} onChange={(e) => set('correspondenceDiffers', e.currentTarget.checked)} />
            {form.correspondenceDiffers && <AddressFields idPrefix="correspondence" value={form.correspondence} onChange={(v) => set('correspondence', v)} />}
            <Switch label={t('business.addresses.billingDiffers')} checked={form.billingDiffers} onChange={(e) => set('billingDiffers', e.currentTarget.checked)} />
            {form.billingDiffers && <AddressFields idPrefix="billing" value={form.billing} onChange={(v) => set('billing', v)} />}
          </Stack>
        </ProgressiveDisclosure>
      </Stack>
    </SectionCard>
  )
}
