import { useState } from 'react'
import { Checkbox, Group, Select, SimpleGrid, Stack, TagsInput, Text, Textarea, TextInput } from '@mantine/core'
import { useTranslation } from 'react-i18next'
import type { TFunction } from 'i18next'
import { InlineEditField, KeyValueRow, OverviewCard, purple, radius, slate, white } from '@nexotec/ui-kit'
import { BlockingFactsStrip } from './BlockingFactsStrip'
import {
  LEGAL_FORM_OPTIONS,
  translatedEmailTypeOptions,
  translatedGenderOptions,
  translatedLanguageOptions,
  translatedLifecycleOptions,
  translatedPaymentTermsOptions,
  translatedPhoneTypeOptions,
  translatedPreferredChannelOptions,
  translatedSalutationOptions,
  translatedSourceOptions,
} from '../../customerOptions'
import { formatCurrencyChf, formatDate } from '../../utils/format'
import type { CountryOption } from '../../hooks/useCountryOptions'
import { useAdvisorOptions } from '../../hooks/useAdvisorOptions'
import { ContactPointsEditor, type ContactPointUpdatePatch } from './ContactPointsEditor'
import { PhoneInput } from '../PhoneInput'
import type { CustomerEmailRead, CustomerPhoneRead, CustomerRead, CustomerUpdateInput, EmailType, PhoneType } from '../../api/types'

/** The domicile address form's own field names — mapped onto
 * CustomerAddressCreate/Update's addressStreet/addressHouseNumber/... at
 * the API boundary (CustomerDetailPage's onSaveAddress), not here. */
export interface AddressDraft {
  street: string
  line2: string
  houseNumber: string
  postalCode: string
  locality: string
  country: string
}

interface OverviewTabProps {
  customer: CustomerRead
  phones: CustomerPhoneRead[]
  emails: CustomerEmailRead[]
  onSaveField: (patch: Partial<CustomerUpdateInput>) => Promise<void>
  isConflict: (err: unknown) => boolean
  onReload: () => void
  // KAN-30: an address is a /customers/{id}/addresses child row (ADR-067),
  // not a PATCH-able field on the customer — CustomerUpdate genuinely has
  // no `address` field. The caller (CustomerDetailPage) owns the
  // POST-if-absent / PATCH-if-present / DELETE-if-cleared decision, same
  // as it owns the phone/email endpoints below.
  onSaveAddress: (draft: AddressDraft) => Promise<void>
  onCreatePhone: (row: { type: PhoneType; value: string }) => Promise<void>
  onUpdatePhone: (id: string, patch: ContactPointUpdatePatch<PhoneType>) => Promise<void>
  onDeletePhone: (id: string) => Promise<void>
  onCreateEmail: (row: { type: EmailType; value: string }) => Promise<void>
  onUpdateEmail: (id: string, patch: ContactPointUpdatePatch<EmailType>) => Promise<void>
  onDeleteEmail: (id: string) => Promise<void>
  // KAN-32 — the `country` reference list, resolved once by the page and
  // passed down for both the nationality field and the address country.
  countryOptions: CountryOption[]
  locale: string
}

interface FieldProps {
  onSaveField: OverviewTabProps['onSaveField']
  isConflict: OverviewTabProps['isConflict']
  onReload: OverviewTabProps['onReload']
  locale: string
  emptyLabel: string
}

function TextField({
  label,
  value,
  patchKey,
  onSaveField,
  isConflict,
  onReload,
  emptyLabel,
}: FieldProps & { label: string; value: string | null; patchKey: keyof CustomerUpdateInput }) {
  return (
    <KeyValueRow label={label}>
      <InlineEditField
        value={value ?? ''}
        isEmpty={!value}
        emptyLabel={emptyLabel}
        onSave={(raw) => onSaveField({ [patchKey]: raw.trim() || null } as Partial<CustomerUpdateInput>)}
        isConflict={isConflict}
        onReload={onReload}
      />
    </KeyValueRow>
  )
}

function DateField({
  label,
  value,
  patchKey,
  onSaveField,
  isConflict,
  onReload,
  locale,
  emptyLabel,
}: FieldProps & { label: string; value: string | null; patchKey: keyof CustomerUpdateInput }) {
  return (
    <KeyValueRow label={label}>
      <InlineEditField
        value={value ? formatDate(value, locale) : ''}
        isEmpty={!value}
        emptyLabel={emptyLabel}
        editValue={value ?? ''}
        onSave={(raw) => onSaveField({ [patchKey]: raw || null } as Partial<CustomerUpdateInput>)}
        isConflict={isConflict}
        onReload={onReload}
        renderEditor={({ value: v, onChange, onKeyDown, onBlur, autoFocus }) => (
          <input
            type="date"
            value={v}
            autoFocus={autoFocus}
            // Stage into the draft and let Enter / blur commit (same as the
            // default text editor). Committing on every change event fired a
            // mid-typing save with '' -> birthDate: null, and one save per
            // intermediate valid date while the user was still picking.
            onChange={(e) => onChange(e.currentTarget.value)}
            onKeyDown={onKeyDown}
            onBlur={onBlur}
            style={{ font: 'inherit', border: `1px solid ${slate[3]}`, borderRadius: radius.sm, padding: '2px 6px' }}
          />
        )}
      />
    </KeyValueRow>
  )
}

function SelectField({
  label,
  value,
  options,
  patchKey,
  clearable,
  onSaveField,
  isConflict,
  onReload,
  emptyLabel,
}: FieldProps & { label: string; value: string | null; options: { value: string; label: string }[]; patchKey: keyof CustomerUpdateInput; clearable?: boolean }) {
  const displayLabel = options.find((o) => o.value === value)?.label
  return (
    <KeyValueRow label={label}>
      <InlineEditField
        value={displayLabel ?? ''}
        isEmpty={!value}
        emptyLabel={emptyLabel}
        editValue={value ?? ''}
        onSave={(raw) => onSaveField({ [patchKey]: raw || null } as Partial<CustomerUpdateInput>)}
        isConflict={isConflict}
        onReload={onReload}
        renderEditor={({ value: v, onCommit, autoFocus }) => (
          <Select
            data={options}
            value={v || null}
            onChange={(next) => onCommit(next ?? '')}
            autoFocus={autoFocus}
            clearable={clearable}
            size="xs"
            comboboxProps={{ withinPortal: true }}
          />
        )}
      />
    </KeyValueRow>
  )
}

/** FR-17 `website` — scheme-normalised server-side, rendered as a live
 * link (region 3). Click the link's row (not the link) to edit. */
function WebsiteField({
  label,
  value,
  onSaveField,
  emptyLabel,
}: Pick<FieldProps, 'onSaveField' | 'emptyLabel'> & { label: string; value: string | null }) {
  const [draft, setDraft] = useState(value ?? '')
  const [editing, setEditing] = useState(false)
  const commit = () => {
    setEditing(false)
    if ((draft.trim() || null) !== (value ?? null)) void onSaveField({ website: draft.trim() || null })
  }
  return (
    <KeyValueRow label={label}>
      {editing ? (
        <TextInput
          autoFocus
          size="xs"
          value={draft}
          onChange={(e) => setDraft(e.currentTarget.value)}
          onKeyDown={(e) => e.key === 'Enter' && commit()}
          onBlur={commit}
        />
      ) : value ? (
        <Group gap={6} wrap="nowrap">
          <a href={value} target="_blank" rel="noreferrer noopener" style={{ color: purple[6] }}>
            {value}
          </a>
          <button type="button" onClick={() => { setDraft(value); setEditing(true) }} style={{ border: 'none', background: 'none', cursor: 'pointer', color: slate[4], fontSize: 12 }}>
            ✎
          </button>
        </Group>
      ) : (
        <span onClick={() => { setDraft(''); setEditing(true) }} style={{ cursor: 'pointer', fontStyle: 'italic', color: slate[3] }}>
          {emptyLabel}
        </span>
      )}
    </KeyValueRow>
  )
}

/** FR-18 region 4 `creditLimit` — advisory only. Shown as CHF; edited as a
 * plain number. */
function MoneyField({
  label,
  value,
  patchKey,
  onSaveField,
  isConflict,
  onReload,
  emptyLabel,
}: FieldProps & { label: string; value: string | null; patchKey: keyof CustomerUpdateInput }) {
  const asNumber = value != null && value !== '' ? Number(value) : null
  return (
    <KeyValueRow label={label}>
      <InlineEditField
        value={asNumber != null && !Number.isNaN(asNumber) ? formatCurrencyChf(asNumber) : ''}
        isEmpty={value == null || value === ''}
        emptyLabel={emptyLabel}
        editValue={value ?? ''}
        onSave={(raw) => onSaveField({ [patchKey]: raw.trim() || null } as Partial<CustomerUpdateInput>)}
        isConflict={isConflict}
        onReload={onReload}
        renderEditor={({ value: v, onChange, onKeyDown, onBlur, autoFocus }) => (
          <input
            type="number"
            step="0.01"
            min="0"
            value={v}
            autoFocus={autoFocus}
            onChange={(e) => onChange(e.currentTarget.value)}
            onKeyDown={onKeyDown}
            onBlur={onBlur}
            style={{ font: 'inherit', border: `1px solid ${slate[3]}`, borderRadius: radius.sm, padding: '2px 6px', width: 120 }}
          />
        )}
      />
    </KeyValueRow>
  )
}

/** FR-17 `notes` — PII by default (audit-logged server-side). Multiline;
 * commits on blur. */
function NotesField({
  label,
  value,
  onSaveField,
  emptyLabel,
}: Pick<FieldProps, 'onSaveField' | 'emptyLabel'> & { label: string; value: string | null }) {
  const [draft, setDraft] = useState(value ?? '')
  const [editing, setEditing] = useState(false)
  return (
    <KeyValueRow label={label}>
      {editing ? (
        <Textarea
          autoFocus
          size="xs"
          autosize
          minRows={2}
          value={draft}
          onChange={(e) => setDraft(e.currentTarget.value)}
          onBlur={() => {
            setEditing(false)
            if ((draft.trim() || null) !== (value ?? null)) void onSaveField({ notes: draft.trim() || null })
          }}
        />
      ) : (
        <span
          onClick={() => {
            setDraft(value ?? '')
            setEditing(true)
          }}
          style={{ cursor: 'pointer', whiteSpace: 'pre-wrap', fontStyle: value ? undefined : 'italic', color: value ? undefined : slate[3] }}
        >
          {value || emptyLabel}
        </span>
      )}
    </KeyValueRow>
  )
}

/** FR-17 `tags` — free per-group labels, full-replace on change. Not a
 * reference list (ADR-058). */
function TagsField({
  label,
  value,
  onSaveField,
}: { label: string; value: string[]; onSaveField: FieldProps['onSaveField'] }) {
  return (
    <KeyValueRow label={label}>
      <TagsInput
        size="xs"
        value={value}
        onChange={(next) => void onSaveField({ tags: next })}
        comboboxProps={{ withinPortal: true }}
      />
    </KeyValueRow>
  )
}

function AddressField({
  customer,
  onSaveAddress,
  countryOptions,
  t,
}: {
  customer: CustomerRead
  onSaveAddress: OverviewTabProps['onSaveAddress']
  countryOptions: CountryOption[]
  t: TFunction
}) {
  const draftFromCustomer = (): AddressDraft => ({
    street: customer.address?.addressStreet ?? '',
    line2: customer.address?.addressLine2 ?? '',
    houseNumber: customer.address?.addressHouseNumber ?? '',
    postalCode: customer.address?.addressPostalCode ?? '',
    locality: customer.address?.addressLocality ?? '',
    country: customer.address?.addressCountry ?? 'CH',
  })
  const [editing, setEditing] = useState(false)
  const [draft, setDraft] = useState<AddressDraft>(draftFromCustomer)
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState<string | null>(null)

  const startEdit = () => {
    setDraft(draftFromCustomer())
    setError(null)
    setEditing(true)
  }

  // No version column on CustomerAddress (same as phone/email, ADR-067) —
  // no 409/If-Match here, so no conflict-specific UI, unlike the
  // versioned scalar fields TextField/SelectField/DateField save above.
  const save = async () => {
    setSaving(true)
    setError(null)
    try {
      await onSaveAddress(draft)
      setEditing(false)
    } catch (err) {
      setError(err instanceof Error ? err.message : t('customerDetail.errors.failedToSave'))
    } finally {
      setSaving(false)
    }
  }

  if (editing) {
    return (
      <div style={{ padding: `8px 0` }}>
        <Stack gap="xs">
          <Group grow gap="xs">
            <TextInput
              size="xs"
              label={t('customerDetail.overview.addressForm.street')}
              value={draft.street}
              onChange={(e) => setDraft({ ...draft, street: e.currentTarget.value })}
            />
            <TextInput
              size="xs"
              label={t('customerDetail.overview.addressForm.houseNumber')}
              value={draft.houseNumber}
              onChange={(e) => setDraft({ ...draft, houseNumber: e.currentTarget.value })}
            />
          </Group>
          <TextInput
            size="xs"
            label={t('customerDetail.overview.addressForm.line2')}
            value={draft.line2}
            onChange={(e) => setDraft({ ...draft, line2: e.currentTarget.value })}
          />
          <Group grow gap="xs">
            <TextInput
              size="xs"
              label={t('customerDetail.overview.addressForm.postalCode')}
              value={draft.postalCode}
              onChange={(e) => setDraft({ ...draft, postalCode: e.currentTarget.value })}
            />
            <TextInput
              size="xs"
              label={t('customerDetail.overview.addressForm.locality')}
              value={draft.locality}
              onChange={(e) => setDraft({ ...draft, locality: e.currentTarget.value })}
            />
          </Group>
          <Select
            size="xs"
            label={t('customerDetail.overview.addressForm.country')}
            data={countryOptions}
            value={draft.country || null}
            onChange={(next) => setDraft({ ...draft, country: next ?? 'CH' })}
            searchable
            allowDeselect={false}
            comboboxProps={{ withinPortal: true }}
          />
          {error && (
            <Text size="xs" c="red">
              {error}
            </Text>
          )}
          <Group gap="xs">
            <button
              type="button"
              onClick={() => void save()}
              disabled={saving}
              style={{ fontSize: 12, fontWeight: 600, color: white, backgroundColor: purple[6], border: 'none', borderRadius: radius.sm, padding: '4px 10px', cursor: 'pointer' }}
            >
              {t('customerDetail.overview.addressForm.save')}
            </button>
            <button
              type="button"
              onClick={() => setEditing(false)}
              disabled={saving}
              style={{ fontSize: 12, fontWeight: 600, color: slate[6], background: 'none', border: 'none', cursor: 'pointer' }}
            >
              {t('customerDetail.overview.addressForm.cancel')}
            </button>
          </Group>
        </Stack>
      </div>
    )
  }

  const addr = customer.address
  const line = addr
    ? `${addr.addressLine2 ? `${addr.addressLine2}, ` : ''}${addr.addressStreet} ${addr.addressHouseNumber}, ${addr.addressPostalCode} ${addr.addressLocality}`
    : ''
  return (
    <KeyValueRow label={t('customerDetail.overview.fields.address')}>
      <span onClick={startEdit} style={{ cursor: 'pointer', fontStyle: addr ? undefined : 'italic', color: addr ? undefined : slate[3] }}>
        {addr ? line : t('customerDetail.overview.notSet')}
      </span>
    </KeyValueRow>
  )
}

export function OverviewTab({
  customer,
  phones,
  emails,
  onSaveField,
  isConflict,
  onReload,
  onSaveAddress,
  onCreatePhone,
  onUpdatePhone,
  onDeletePhone,
  onCreateEmail,
  onUpdateEmail,
  onDeleteEmail,
  countryOptions,
  locale,
}: OverviewTabProps) {
  const { t } = useTranslation()
  const fieldProps: FieldProps = { onSaveField, isConflict, onReload, locale, emptyLabel: t('customerDetail.overview.notSet') }
  const f = t('customerDetail.overview.fields', { returnObjects: true }) as Record<string, string>
  // KAN-50 / D-24 — active users of the acting dealership, for the advisor
  // picker. The current advisor's stored label still renders when they are
  // not in this list (e.g. resolved by another dealership).
  const { options: advisorOptions } = useAdvisorOptions()
  const advisorSelectOptions =
    customer.advisorId && !advisorOptions.some((o) => o.value === customer.advisorId)
      ? [...advisorOptions, { value: customer.advisorId, label: customer.advisorLabel ?? customer.advisorId }]
      : advisorOptions

  return (
    <Stack gap="md">
      {/* FR-18 region 1 (KAN-44) — blocking facts sit ABOVE the cards, not
          inside one; absent entirely when neither applies. */}
      <BlockingFactsStrip customer={customer} />

      <SimpleGrid cols={{ base: 1, md: 2 }} spacing="md">
        <OverviewCard title={t('customerDetail.overview.cards.identity')}>
        {customer.customerType === 'individual' ? (
          <>
            <SelectField label={f.salutation} value={customer.salutation} options={translatedSalutationOptions(t)} patchKey="salutation" clearable {...fieldProps} />
            <TextField label={f.firstName} value={customer.firstName} patchKey="firstName" {...fieldProps} />
            <TextField label={f.lastName} value={customer.lastName} patchKey="lastName" {...fieldProps} />
            {/* FR-17 — free text, rendered after the salutation in the letter opening. */}
            <TextField label={f.title} value={customer.title} patchKey="title" {...fieldProps} />
            <DateField label={f.dateOfBirth} value={customer.birthDate} patchKey="birthDate" {...fieldProps} />
            {/* KAN-32 — chosen from the `country` reference list, not typed. */}
            <SelectField
              label={f.nationality}
              value={customer.nationality}
              options={countryOptions}
              patchKey="nationality"
              clearable
              {...fieldProps}
            />
            {/* FR-17 — segmentation only, distinct from salutation, never inferred. */}
            <SelectField label={f.gender} value={customer.gender} options={translatedGenderOptions(t)} patchKey="gender" {...fieldProps} />
          </>
        ) : (
          <>
            <TextField label={f.companyName} value={customer.companyName} patchKey="companyName" {...fieldProps} />
            {/* Legal form labels (AG/GmbH/Sàrl/Sagl…) carry real legal meaning that
                differs by Swiss language region — left as the standard Swiss
                designation rather than a guessed FR/IT translation. Same call as
                canton names. */}
            <SelectField label={f.legalForm} value={customer.legalForm} options={LEGAL_FORM_OPTIONS} patchKey="legalForm" clearable {...fieldProps} />
          </>
        )}
        <SelectField label={f.correspondenceLanguage} value={customer.language} options={translatedLanguageOptions(t)} patchKey="language" {...fieldProps} />
        <SelectField
          label={f.preferredChannel}
          value={customer.preferredChannel}
          options={translatedPreferredChannelOptions(t)}
          patchKey="preferredChannel"
          clearable
          {...fieldProps}
        />
      </OverviewCard>

      <OverviewCard title={t('customerDetail.overview.cards.status')}>
        <SelectField label={f.lifecycleStatus} value={customer.lifecycleStatus} options={translatedLifecycleOptions(t)} patchKey="lifecycleStatus" {...fieldProps} />
        <SelectField label={f.source} value={customer.source} options={translatedSourceOptions(t)} patchKey="source" clearable {...fieldProps} />
        <KeyValueRow label={f.marketingConsent}>
          <Checkbox
            checked={customer.marketingConsent}
            onChange={(e) => void onSaveField({ marketingConsent: e.currentTarget.checked })}
            styles={{ root: { display: 'inline-flex' } }}
          />
        </KeyValueRow>
      </OverviewCard>

      {/* FR-18 region 4 — "on what terms may I sell to them?". Finance's
          region. Placed here in FR-18 order (region 4 before region 5); a
          full re-ordering of this whole tab into FR-18's region sequence
          is a separate FR-18 conformance task. */}
      <OverviewCard title={t('customerDetail.overview.cards.commercialStanding')}>
        <SelectField
          label={f.paymentTerms}
          value={customer.paymentTerms}
          options={translatedPaymentTermsOptions(t)}
          patchKey="paymentTerms"
          clearable
          {...fieldProps}
        />
        {/* Advisory only — surfaced, never enforced (FR-18 region 4). */}
        <MoneyField label={f.creditLimit} value={customer.creditLimit} patchKey="creditLimit" {...fieldProps} />
        <TextField label={f.iban} value={customer.iban} patchKey="iban" {...fieldProps} />
        <KeyValueRow label={f.vatRegistered}>
          <Checkbox
            checked={customer.vatRegistered}
            onChange={(e) => void onSaveField({ vatRegistered: e.currentTarget.checked })}
            styles={{ root: { display: 'inline-flex' } }}
          />
        </KeyValueRow>
      </OverviewCard>

      {/* FR-18 region 5 — "what is our history and what happens next?".
          `website` is spec-region-3; grouped here pending the tab re-order. */}
      <OverviewCard title={t('customerDetail.overview.cards.relationship')}>
        <SelectField
          label={f.advisor}
          value={customer.advisorId}
          options={advisorSelectOptions}
          patchKey="advisorId"
          clearable
          {...fieldProps}
        />
        <DateField label={f.customerSince} value={customer.customerSince} patchKey="customerSince" {...fieldProps} />
        <DateField label={f.nextFollowUp} value={customer.nextFollowUp} patchKey="nextFollowUp" {...fieldProps} />
        <WebsiteField label={f.website} value={customer.website} onSaveField={onSaveField} emptyLabel={fieldProps.emptyLabel} />
        <KeyValueRow label={f.newsletter}>
          <Checkbox
            checked={customer.newsletter}
            onChange={(e) => void onSaveField({ newsletter: e.currentTarget.checked })}
            styles={{ root: { display: 'inline-flex' } }}
          />
        </KeyValueRow>
        <TagsField label={f.tags} value={customer.tags ?? []} onSaveField={onSaveField} />
        <NotesField label={f.notes} value={customer.notes} onSaveField={onSaveField} emptyLabel={fieldProps.emptyLabel} />
      </OverviewCard>

      <OverviewCard title={t('customerDetail.overview.cards.address')}>
        <AddressField customer={customer} onSaveAddress={onSaveAddress} countryOptions={countryOptions} t={t} />
        {/* Derived server-side from the postal code (D-13), never an
            input — labelled as such so it doesn't read as an empty box
            waiting to be filled in (KAN-30). */}
        <KeyValueRow label={f.cantonDerived}>
          <span style={{ fontStyle: customer.address?.addressCanton ? undefined : 'italic', color: customer.address?.addressCanton ? undefined : slate[3] }}>
            {customer.address?.addressCanton ?? t('customerDetail.overview.notSet')}
          </span>
        </KeyValueRow>
      </OverviewCard>

      <OverviewCard title={t('customerDetail.overview.cards.contactPoints')}>
        <Stack gap="md">
          <ContactPointsEditor<PhoneType>
            label={t('customerDetail.contactPoints.phoneNumbers')}
            addLabel={t('customerDetail.contactPoints.addPhone')}
            typeOptions={translatedPhoneTypeOptions(t)}
            rows={phones.map((p) => ({
              id: p.id,
              type: p.phoneType,
              value: p.phoneE164,
              label: p.label,
              isPrimary: p.isPrimary,
              validTo: p.validTo,
              doNotUse: p.doNotUse,
              doNotUseReason: p.doNotUseReason,
              consentGranted: p.consentGranted,
              consentSource: p.consentSource,
              consentTimestamp: p.consentTimestamp,
            }))}
            newRowType="mobile"
            renderValueEditor={(value, onChange) => <PhoneInput label={t('customerDetail.phoneInput.country')} value={value} onChange={onChange} />}
            onCreate={onCreatePhone}
            onUpdate={onUpdatePhone}
            onDelete={onDeletePhone}
          />
          <ContactPointsEditor<EmailType>
            label={t('customerDetail.contactPoints.emailAddresses')}
            addLabel={t('customerDetail.contactPoints.addEmail')}
            typeOptions={translatedEmailTypeOptions(t)}
            rows={emails.map((e) => ({
              id: e.id,
              type: e.emailType,
              value: e.emailAddress,
              label: e.label,
              isPrimary: e.isPrimary,
              validTo: e.validTo,
              doNotUse: e.doNotUse,
              doNotUseReason: e.doNotUseReason,
              consentGranted: e.consentGranted,
              consentSource: e.consentSource,
              consentTimestamp: e.consentTimestamp,
            }))}
            newRowType="personal"
            renderValueEditor={(value, onChange, autoFocus) => (
              <TextInput size="xs" type="email" value={value} onChange={(e) => onChange(e.currentTarget.value)} autoFocus={autoFocus} />
            )}
            onCreate={onCreateEmail}
            onUpdate={onUpdateEmail}
            onDelete={onDeleteEmail}
          />
        </Stack>
      </OverviewCard>

      <OverviewCard title={t('customerDetail.overview.cards.record')}>
        <KeyValueRow label={f.customerNumber}>
          <span style={{ fontFamily: 'ui-monospace, SF Mono, Menlo, monospace' }}>{customer.customerNumber}</span>
        </KeyValueRow>
        <KeyValueRow label={f.created}>{formatDate(customer.createdAt, locale)}</KeyValueRow>
        <KeyValueRow label={f.lastChanged}>{formatDate(customer.updatedAt, locale)}</KeyValueRow>
      </OverviewCard>
      </SimpleGrid>
    </Stack>
  )
}
