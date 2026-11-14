import { useCallback } from 'react'
import { Anchor, Group, MultiSelect, Select, SimpleGrid, Stack, Text, Textarea, TextInput } from '@mantine/core'
import { useQuery } from '@tanstack/react-query'
import { useTranslation } from 'react-i18next'
import { Building, Contact, MapPin, SlidersHorizontal } from 'lucide-react'
import { ProgressiveDisclosure } from '@nexotec/ui-kit'
import { api, ApiError } from '../../api/client'
import type { BankAccountList, LocationRead, LocationType, LocationUpdate } from '../../api/types'
import { formatIban } from '../../businessOptions'
import { CANTON_OPTIONS } from '../../shared/cantons'
import { useFieldHint } from '../../hooks/useFieldHint'
import { AddressSearchField } from '../AddressSearchField'
import { bankAccountsKey } from '../business/queryKeys'
import { SectionCard } from '../business/SectionCard'
import { useRecordForm } from '../business/useRecordForm'
import { locationTypeOptions, mapUrl } from './locationOptions'
import { coordinate, orNull, useLocationPatch } from './useLocationPatch'

export interface SectionProps {
  location: LocationRead
  dealershipId: string
  canWrite: boolean
}

const same = (a: unknown, b: unknown) => JSON.stringify(a) === JSON.stringify(b)

/** Only the fields that moved — a PATCH carries the section's changes, never the whole record. */
function changed(loaded: LocationRead, next: LocationUpdate): LocationUpdate {
  const out: Record<string, unknown> = {}
  for (const [key, value] of Object.entries(next)) {
    if (!same(value, (loaded as unknown as Record<string, unknown>)[key])) out[key] = value
  }
  return out as LocationUpdate
}

// --- Master data ------------------------------------------------------------------------------

interface MasterForm {
  name: string
  code: string
  locationTypes: LocationType[]
  validFrom: string
  validTo: string
}

const masterForm = (l: LocationRead): MasterForm => ({
  name: l.name,
  code: l.code ?? '',
  locationTypes: l.locationTypes,
  validFrom: l.validFrom ?? '',
  validTo: l.validTo ?? '',
})

/** Name, code (set once — A-RULE-1 refers by id, documents print the code), what the site is, and its validity. */
export function MasterDataSection({ location, dealershipId, canWrite }: SectionProps) {
  const { t } = useTranslation()
  const hint = useFieldHint()
  const { form, set, dirty, markSaved, loaded } = useRecordForm(location, useCallback(masterForm, []))
  const patch = useLocationPatch(dealershipId, location.id)
  const codeFixed = loaded.code !== null
  const changes = changed(loaded, {
    name: form.name.trim(),
    ...(codeFixed ? {} : form.code.trim() ? { code: form.code.trim() } : {}),
    locationTypes: form.locationTypes,
    validFrom: form.validFrom || null,
    validTo: form.validTo || null,
  })
  return (
    <SectionCard
      title={t('locations.master.title')}
      icon={<Building size={20} />}
      onSave={canWrite ? () => patch.save({ changes, version: loaded.version }, { onSuccess: markSaved }) : undefined}
      dirty={dirty && Object.keys(changes).length > 0}
      saveDisabled={form.name.trim() === ''}
      saving={patch.saving}
      error={patch.error}
      onDismissError={patch.clearError}
      saved={patch.saved}
    >
      <Stack gap="sm">
        <SimpleGrid cols={{ base: 1, sm: 2 }}>
          <TextInput label={t('locations.master.name')} value={form.name} onChange={(e) => set('name', e.currentTarget.value)} readOnly={!canWrite} maxLength={200} required />
          <TextInput
            {...hint(t('locations.master.code'), codeFixed ? t('locations.master.codeFixed') : t('locations.master.codeHint'))}
            value={form.code}
            onChange={(e) => set('code', e.currentTarget.value.toUpperCase())}
            readOnly={!canWrite || codeFixed}
            maxLength={20}
            styles={{ input: { fontFamily: 'var(--mantine-font-family-monospace)' } }}
          />
        </SimpleGrid>
        <MultiSelect
          {...hint(t('locations.master.types'), t('locations.master.typesHint'))}
          data={locationTypeOptions(t)}
          value={form.locationTypes}
          onChange={(v) => set('locationTypes', v as LocationType[])}
          readOnly={!canWrite}
        />
        <SimpleGrid cols={{ base: 1, sm: 2 }}>
          <TextInput label={t('locations.master.validFrom')} type="date" value={form.validFrom} onChange={(e) => set('validFrom', e.currentTarget.value)} readOnly={!canWrite} />
          <TextInput label={t('locations.master.validTo')} type="date" value={form.validTo} onChange={(e) => set('validTo', e.currentTarget.value)} readOnly={!canWrite} />
        </SimpleGrid>
      </Stack>
    </SectionCard>
  )
}

// --- Address and map ---------------------------------------------------------------------------

interface AddressForm {
  street: string
  houseNumber: string
  postalCode: string
  locality: string
  canton: string | null
  country: string
  geoLat: string
  geoLng: string
}

const addressForm = (l: LocationRead): AddressForm => ({
  street: l.addressStreet ?? '',
  houseNumber: l.addressHouseNumber ?? '',
  postalCode: l.addressPostalCode ?? '',
  locality: l.addressLocality ?? '',
  canton: l.addressCanton,
  country: l.addressCountry ?? 'CH',
  geoLat: l.geoLat === null ? '' : String(l.geoLat),
  geoLng: l.geoLng === null ? '' : String(l.geoLng),
})

/**
 * The site's address and its map pin. The pin is the stored coordinates,
 * shown as numbers and opened in OpenStreetMap by a link — the app loads no
 * map tiles (no map service is part of the platform).
 */
export function AddressSection({ location, dealershipId, canWrite }: SectionProps) {
  const { t } = useTranslation()
  const hint = useFieldHint()
  const { form, set, dirty, markSaved, loaded } = useRecordForm(location, useCallback(addressForm, []))
  const patch = useLocationPatch(dealershipId, location.id)
  const country = form.country.trim().toUpperCase() || 'CH'
  const swiss = country === 'CH'
  const lat = coordinate(form.geoLat)
  const lng = coordinate(form.geoLng)
  const geoValid = lat !== 'invalid' && lng !== 'invalid'
  const changes = changed(loaded, {
    addressStreet: orNull(form.street),
    addressHouseNumber: orNull(form.houseNumber),
    addressPostalCode: orNull(form.postalCode),
    addressLocality: orNull(form.locality),
    addressCanton: swiss ? form.canton : null,
    addressCountry: country,
    ...(geoValid ? { geoLat: lat, geoLng: lng } : {}),
  })
  return (
    <SectionCard
      title={t('locations.address.title')}
      icon={<MapPin size={20} />}
      onSave={canWrite ? () => patch.save({ changes, version: loaded.version }, { onSuccess: markSaved }) : undefined}
      dirty={dirty && Object.keys(changes).length > 0}
      saveDisabled={!geoValid}
      saving={patch.saving}
      error={patch.error}
      onDismissError={patch.clearError}
      saved={patch.saved}
    >
      <Stack gap="sm">
        {canWrite && (
          <AddressSearchField
            id="location-address-search"
            country={country}
            onSelect={(address) => {
              set('street', address.street)
              set('houseNumber', address.houseNumber)
              set('locality', address.locality)
              // D-13: the canton is never Google's — a pick that moves the postal code clears it.
              if (address.postalCode !== form.postalCode) set('canton', null)
              set('postalCode', address.postalCode)
            }}
          />
        )}
        <SimpleGrid cols={{ base: 1, sm: 3 }}>
          <TextInput label={t('business.addresses.street')} value={form.street} onChange={(e) => set('street', e.currentTarget.value)} readOnly={!canWrite} />
          <TextInput label={t('business.addresses.houseNumber')} value={form.houseNumber} onChange={(e) => set('houseNumber', e.currentTarget.value)} readOnly={!canWrite} />
          <TextInput label={t('business.addresses.postalCode')} value={form.postalCode} onChange={(e) => set('postalCode', e.currentTarget.value)} readOnly={!canWrite} />
          <TextInput label={t('business.addresses.locality')} value={form.locality} onChange={(e) => set('locality', e.currentTarget.value)} readOnly={!canWrite} />
          {swiss && (
            <Select label={t('business.addresses.canton')} data={CANTON_OPTIONS} value={form.canton} onChange={(v) => set('canton', v)} searchable readOnly={!canWrite} />
          )}
          <TextInput
            {...hint(t('business.addresses.country'), t('business.addresses.countryHint'))}
            value={form.country}
            onChange={(e) => set('country', e.currentTarget.value.toUpperCase())}
            maxLength={2}
            readOnly={!canWrite}
          />
        </SimpleGrid>
        <SimpleGrid cols={{ base: 1, sm: 2 }}>
          <TextInput
            {...hint(t('locations.address.geoLat'), t('locations.address.geoHint'))}
            value={form.geoLat}
            onChange={(e) => set('geoLat', e.currentTarget.value)}
            error={lat === 'invalid' ? t('business.errors.invalid') : undefined}
            readOnly={!canWrite}
            inputMode="decimal"
          />
          <TextInput
            label={t('locations.address.geoLng')}
            value={form.geoLng}
            onChange={(e) => set('geoLng', e.currentTarget.value)}
            error={lng === 'invalid' ? t('business.errors.invalid') : undefined}
            readOnly={!canWrite}
            inputMode="decimal"
          />
        </SimpleGrid>
        <MapPinLine lat={loaded.geoLat} lng={loaded.geoLng} />
      </Stack>
    </SectionCard>
  )
}

function MapPinLine({ lat, lng }: { lat: number | null; lng: number | null }) {
  const { t } = useTranslation()
  if (lat === null || lng === null) {
    return (
      <Text c="dimmed" fz="sm" fs="italic">
        {t('locations.address.noGeo')}
      </Text>
    )
  }
  return (
    <Group gap="xs" role="group" aria-label={t('locations.address.pin')}>
      <MapPin size={16} aria-hidden />
      <Text ff="monospace" fz="sm">
        {lat.toFixed(6)}, {lng.toFixed(6)}
      </Text>
      <Anchor href={mapUrl(lat, lng)} target="_blank" rel="noopener noreferrer" fz="sm">
        {t('locations.address.openMap')}
      </Anchor>
    </Group>
  )
}

// --- Contacts ------------------------------------------------------------------------------------

interface ContactsForm {
  contactPerson: string
  phone: string
  email: string
  leadEmail: string
}

const contactsForm = (l: LocationRead): ContactsForm => ({
  contactPerson: l.contactPerson ?? '',
  phone: l.phone ?? '',
  email: l.email ?? '',
  leadEmail: l.leadEmail ?? '',
})

/** Per site: who to ask, how to reach the site, and where marketplace enquiries go (`leadEmail`). */
export function ContactsSection({ location, dealershipId, canWrite }: SectionProps) {
  const { t } = useTranslation()
  const hint = useFieldHint()
  const { form, set, dirty, markSaved, loaded } = useRecordForm(location, useCallback(contactsForm, []))
  const patch = useLocationPatch(dealershipId, location.id)
  const changes = changed(loaded, {
    contactPerson: orNull(form.contactPerson),
    phone: orNull(form.phone.replace(/\s+/g, '')),
    email: orNull(form.email),
    leadEmail: orNull(form.leadEmail),
  })
  return (
    <SectionCard
      title={t('locations.contacts.title')}
      icon={<Contact size={20} />}
      onSave={canWrite ? () => patch.save({ changes, version: loaded.version }, { onSuccess: markSaved }) : undefined}
      dirty={dirty && Object.keys(changes).length > 0}
      saving={patch.saving}
      error={patch.error}
      onDismissError={patch.clearError}
      saved={patch.saved}
    >
      <SimpleGrid cols={{ base: 1, sm: 2 }}>
        <TextInput label={t('locations.contacts.contactPerson')} value={form.contactPerson} onChange={(e) => set('contactPerson', e.currentTarget.value)} readOnly={!canWrite} />
        <TextInput label={t('locations.contacts.phone')} value={form.phone} onChange={(e) => set('phone', e.currentTarget.value)} placeholder="+41 44 …" readOnly={!canWrite} />
        <TextInput label={t('locations.contacts.email')} type="email" value={form.email} onChange={(e) => set('email', e.currentTarget.value)} readOnly={!canWrite} />
        <TextInput
          {...hint(t('locations.contacts.leadEmail'), t('locations.contacts.leadEmailHint'))}
          type="email"
          value={form.leadEmail}
          onChange={(e) => set('leadEmail', e.currentTarget.value)}
          readOnly={!canWrite}
        />
      </SimpleGrid>
    </SectionCard>
  )
}

// --- Overrides -------------------------------------------------------------------------------------

interface OverridesForm {
  letterhead: string
  bankAccountId: string | null
}

const overridesForm = (l: LocationRead): OverridesForm => ({
  letterhead: l.letterheadAddressOverride ?? '',
  bankAccountId: l.bankAccountOverrideId,
})

/**
 * What this site does differently from the Betrieb — collapsed, because
 * most sites inherit everything. The bank account exists only for the roles
 * that read banking (ADR-100): for anyone else the API answers 404 and the
 * field is not shown.
 */
export function OverridesSection({ location, dealershipId, canWrite }: SectionProps) {
  const { t } = useTranslation()
  const hint = useFieldHint()
  const { form, set, dirty, markSaved, loaded } = useRecordForm(location, useCallback(overridesForm, []))
  const patch = useLocationPatch(dealershipId, location.id)
  const accounts = useQuery({
    queryKey: bankAccountsKey(dealershipId),
    queryFn: () => api.get<BankAccountList>(`/dealerships/${dealershipId}/bank-accounts`),
    retry: false,
  })
  const bankingHidden = accounts.error instanceof ApiError && accounts.error.status === 404
  const accountOptions = (accounts.data?.items ?? []).map((a) => ({ value: a.id, label: [a.label, formatIban(a.iban)].filter(Boolean).join(' · ') }))
  const changes = changed(loaded, {
    letterheadAddressOverride: orNull(form.letterhead),
    ...(bankingHidden ? {} : { bankAccountOverrideId: form.bankAccountId }),
  })
  return (
    <SectionCard
      title={t('locations.overrides.title')}
      icon={<SlidersHorizontal size={20} />}
      onSave={canWrite ? () => patch.save({ changes, version: loaded.version }, { onSuccess: markSaved }) : undefined}
      dirty={dirty && Object.keys(changes).length > 0}
      saving={patch.saving}
      error={patch.error}
      onDismissError={patch.clearError}
      saved={patch.saved}
    >
      <ProgressiveDisclosure label={t('locations.overrides.disclosure')}>
        <Stack gap="sm">
          <Textarea
            {...hint(t('locations.overrides.letterhead'), t('locations.overrides.letterheadHint'))}
            value={form.letterhead}
            onChange={(e) => set('letterhead', e.currentTarget.value)}
            autosize
            minRows={3}
            maxLength={500}
            readOnly={!canWrite}
          />
          {!bankingHidden && (
            <Select
              label={t('locations.overrides.bankAccount')}
              placeholder={t('locations.overrides.bankAccountInherit')}
              data={accountOptions}
              value={form.bankAccountId}
              onChange={(v) => set('bankAccountId', v)}
              clearable
              readOnly={!canWrite}
            />
          )}
        </Stack>
      </ProgressiveDisclosure>
    </SectionCard>
  )
}
