import { useEffect, useMemo, useState, type Dispatch, type ReactNode, type SetStateAction } from 'react'
import { Checkbox, Group, MultiSelect, Select, SimpleGrid, Stack, Text, TextInput, UnstyledButton } from '@mantine/core'
import { useDebouncedValue } from '@mantine/hooks'
import { useForm } from '@mantine/form'
import { useTranslation } from 'react-i18next'
import { Building2, User } from 'lucide-react'
import {
  RepeatableRowGroup,
  Wizard,
  purple,
  slate,
  type RepeatableRowPatch,
  type RepeatableRowValue,
  type WizardStep,
} from '@nexotec/ui-kit'
import { useFieldHint } from '../../../hooks/useFieldHint'
import { useCanWrite } from '../../../auth/useCanWrite'
import { api } from '../../../api/client'
import {
  LANGUAGE_OPTIONS,
  translatedEmailTypeOptions,
  translatedLifecycleOptions,
  translatedPhoneTypeOptions,
  translatedPreferredChannelOptions,
  translatedSalutationOptions,
  translatedSourceOptions,
  translatedLegalFormOptions,
  translatedRoleOptions,
} from '../contactOptions'
import { SUPPORTED_LANGUAGES } from '../../../i18n'
import { AddressSearchField } from '../../../components/AddressSearchField'
import { DuplicateWarningPanel } from './DuplicateWarningPanel'
import { contactPointRowLabels } from './contact-detail/contactPointRowLabels'
import { COMPACT_PHONE_INPUT_MIN_WIDTH, PhoneInput } from '../../../components/PhoneInput'
import { useCountryOptions } from '../../../hooks/useCountryOptions'
import { useIdempotencyKey } from '../../../hooks/useIdempotencyKey'
import type {
  ContactRole,
  ContactCreateInput,
  ContactDuplicateCandidate,
  ContactDuplicateCandidateList,
  ContactLifecycleStatus,
  ContactRead,
  ContactSource,
  ContactType,
  EmailType,
  Language,
  LegalForm,
  PhoneType,
  PreferredChannel,
  Salutation,
} from '../../../api/types'
import { apiRefusalText } from '../../../utils/apiRefusal'

interface FormValues {
  language: Language
  salutation: Salutation | ''
  firstName: string
  lastName: string
  birthDate: string
  nationality: string
  companyName: string
  legalForm: LegalForm | ''
  taxId: string
  preferredChannel: PreferredChannel | ''
  lifecycleStatus: ContactLifecycleStatus
  source: ContactSource | ''
  marketingConsent: boolean
  hasAddress: boolean
  street: string
  line2: string
  houseNumber: string
  postalCode: string
  locality: string
  // ISO 3166-1 alpha-2, defaults to CH — validated against the `country`
  // reference list server-side (KAN-32).
  country: string
}

const EMPTY_VALUES: FormValues = {
  language: 'de',
  salutation: '',
  firstName: '',
  lastName: '',
  birthDate: '',
  nationality: '',
  companyName: '',
  legalForm: '',
  taxId: '',
  preferredChannel: '',
  lifecycleStatus: 'prospect',
  source: '',
  marketingConsent: false,
  hasAddress: false,
  street: '',
  line2: '',
  houseNumber: '',
  postalCode: '',
  locality: '',
  country: 'CH',
}

export interface ContactCreateFlowProps {
  onSuccess: (contact: ContactRead) => void;
  onCancel: () => void;
  /** Pre-selects a type and skips straight to step 2 — e.g. an "Add
   * business contact" entry point elsewhere in the app. */
  initialContactType?: ContactType;
  /** FR-04: "shows matches in a side panel with an 'open this contact
   * instead' action." Omit to hide the "Open instead" affordance (e.g.
   * an embedding context with nowhere sensible to navigate to). */
  onOpenExisting?: (contactId: string) => void;
}

/**
 * FR-03: "Two-step form. Step 1 chooses individual or business, which is
 * then immutable. Step 2 shows only the fields valid for [type]."
 *
 * This is the reusable container, not a page — it owns the wizard state,
 * validation, and the actual POST /contacts call, but takes onSuccess /
 * onCancel callbacks instead of navigating itself and renders no page-level
 * chrome (no Container/Title). That's deliberate: the "Add New Contact"
 * feature note says this process "should be reusable in different parts of
 * the DMS" — ContactCreatePage renders this full-page for /contacts/new,
 * and the same component can later be dropped into a Modal from e.g. a
 * future Offer-creation screen with different onSuccess/onCancel wiring,
 * with zero duplication of the two-step logic, validation, or the API call.
 */
export function ContactCreateFlow({ onSuccess, onCancel, initialContactType, onOpenExisting }: ContactCreateFlowProps) {
  const { t, i18n } = useTranslation()
  const hint = useFieldHint()
  const steps: WizardStep[] = [
    { id: 'type', label: t('contactCreate.steps.type') },
    { id: 'details', label: t('contactCreate.steps.details') },
  ]
  const [step, setStep] = useState(initialContactType ? 1 : 0)
  const [contactType, setContactType] = useState<ContactType>(initialContactType ?? 'individual')
  // KAN-464: the roles the new contact holds — at least one; a contact
  // unless the user chooses otherwise. Without the customer role the contact
  // has no lifecycle status of its own (Q1), so the field is not offered.
  // Q3: a user who may not give the customer role (inventory) creates
  // suppliers — the contact option is shown, not selectable.
  const mayWriteCustomerRole = useCanWrite('contact_customer_role')
  // Until the user picks, the default follows the session (it may still be loading).
  const [chosenRoles, setRoles] = useState<ContactRole[] | null>(null)
  const roles: ContactRole[] = chosenRoles ?? (mayWriteCustomerRole ? ['customer'] : ['supplier'])
  const roleOptions = translatedRoleOptions(t).map((option) =>
    option.value === 'customer' && !mayWriteCustomerRole ? { ...option, disabled: true } : option,
  )
  const holdsCustomer = roles.includes('customer')
  // § ADR-067 — the same RepeatableRowGroup the detail screen uses, in
  // `create` mode: one primary per type-group, marking a new primary
  // unmarks the old in the same interaction, consent on the row. Held in
  // memory (no contact exists yet) and flushed with the create POST; the
  // per-mutation callbacks are the local-array equivalent of the detail
  // screen's PATCH calls.
  const [phones, setPhones] = useState<RepeatableRowValue[]>([])
  const [emails, setEmails] = useState<RepeatableRowValue[]>([])
  const phoneRowGroup = useMemo(() => controlledRowGroup(setPhones), [])
  const emailRowGroup = useMemo(() => controlledRowGroup(setEmails), [])
  const [submitting, setSubmitting] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [duplicates, setDuplicates] = useState<ContactDuplicateCandidate[]>([])

  // Reused across retries of the same logical submit attempt (e.g. a
  // network error and a re-click of Submit) so the backend's idempotency
  // check — POST /contacts accepts an Idempotency-Key header — collapses
  // them into one contact instead of creating duplicates.
  const idempotencyKey = useIdempotencyKey()

  // FR-03 / FR-13: correspondence language is a CONTACT field, but it
  // pre-fills from the acting user's current UI language (and stays freely
  // overridable). It is never bound to i18n.language after this initial seed.
  // Take the base subtag so a region-tagged UI language ("fr-CH") still seeds
  // "fr" rather than silently falling back to "de".
  const uiBaseLanguage = i18n.language.split('-')[0]
  const initialLanguage: Language = (SUPPORTED_LANGUAGES as readonly string[]).includes(uiBaseLanguage)
    ? (uiBaseLanguage as Language)
    : 'de'
  const form = useForm<FormValues>({ initialValues: { ...EMPTY_VALUES, language: initialLanguage } })
  const { options: countryOptions } = useCountryOptions()

  // FR-04: "While the user types name, email or phone in the create form,
  // the system queries a duplicate-check endpoint (debounced, per
  // keystroke)... never a blocking gate." The endpoint takes a single `q`
  // matched broadly server-side against name/company/email/phone, so one
  // debounced query is enough — priority order (name, then email, then
  // phone) picks whichever field is most likely to be the one the user is
  // currently filling in, per US-02's own example ("while I type a new
  // contact's name").
  const dedupeSignal =
    (contactType === 'individual' ? form.values.lastName : form.values.companyName).trim() ||
    emails[0]?.value.trim() ||
    phones[0]?.value.trim() ||
    ''
  const [debouncedDedupeSignal] = useDebouncedValue(dedupeSignal, 350)

  useEffect(() => {
    if (debouncedDedupeSignal.length < 2) {
      setDuplicates([])
      return
    }
    let cancelled = false
    api
      .get<ContactDuplicateCandidateList>(`/contacts/duplicate-check?q=${encodeURIComponent(debouncedDedupeSignal)}`)
      .then((res) => {
        if (!cancelled) setDuplicates(res.items)
      })
      .catch(() => {
        if (!cancelled) setDuplicates([])
      })
    return () => {
      cancelled = true
    }
  }, [debouncedDedupeSignal])

  const handleNext = () => {
    if (step === 0) {
      setStep(1)
      return
    }
    void handleSubmit()
  }

  const handleSubmit = async () => {
    const values = form.values
    setError(null)

    if (contactType === 'individual' && (!values.firstName.trim() || !values.lastName.trim())) {
      setError(t('contactCreate.validation.individualNameRequired'))
      return
    }
    if (contactType === 'business' && !values.companyName.trim()) {
      setError(t('contactCreate.validation.companyNameRequired'))
      return
    }
    if (roles.length === 0) {
      setError(t('contactCreate.validation.roleRequired'))
      return
    }
    const hasContactPoint = phones.some((p) => p.value) || emails.some((e) => e.value)
    if (!hasContactPoint) {
      setError(t('contactCreate.validation.contactPointRequired'))
      return
    }

    setSubmitting(true)
    try {
      const payload: ContactCreateInput = {
        contactType,
        language: values.language,
        salutation: values.salutation || null,
        preferredChannel: values.preferredChannel || null,
        phones: phones
          .filter((p) => p.value.trim())
          .map((p) => ({ phoneType: p.type as PhoneType, phoneE164: p.value, isPrimary: p.isPrimary, label: p.label ?? null })),
        emails: emails
          .filter((e) => e.value.trim())
          .map((e) => ({ emailType: e.type as EmailType, emailAddress: e.value, isPrimary: e.isPrimary, label: e.label ?? null })),
        // KAN-30: ContactCreate expects addresses: ContactAddressCreate[]
        // (ADR-067 child rows), never a flat `address` field.
        addresses: values.hasAddress
          ? [
              {
                addressType: 'domicile',
                addressStreet: values.street,
                addressLine2: values.line2 || null,
                addressHouseNumber: values.houseNumber,
                addressPostalCode: values.postalCode,
                addressLocality: values.locality,
                addressCountry: values.country || 'CH',
                isPrimary: true,
              },
            ]
          : [],
        roles,
        ...(holdsCustomer ? { lifecycleStatus: values.lifecycleStatus } : {}),
        source: values.source || null,
        marketingConsent: values.marketingConsent,
        ...(contactType === 'individual'
          ? { firstName: values.firstName, lastName: values.lastName, birthDate: values.birthDate || null, nationality: values.nationality || null }
          : { companyName: values.companyName, legalForm: values.legalForm || null, taxId: values.taxId || null }),
      }
      const created = await api.post<ContactRead>('/contacts', payload, idempotencyKey.headers())
      idempotencyKey.renew()
      onSuccess(created)
    } catch (err) {
      setError(apiRefusalText(t, err, 'contactCreate.errors.createFailed'))
    } finally {
      setSubmitting(false)
    }
  }

  return (
    <Wizard
      steps={steps}
      activeIndex={step}
      onBack={() => setStep(0)}
      onNext={handleNext}
      onCancel={onCancel}
      nextLabel={t('contactCreate.actions.next')}
      backLabel={t('contactCreate.actions.back')}
      cancelLabel={t('contactCreate.actions.cancel')}
      submitLabel={t('contactCreate.actions.submit')}
      submitting={submitting}
      error={error}
    >
      {step === 0 ? (
        <SimpleGrid cols={2} spacing="md">
          <TypeOption
            icon={<User size={18} />}
            label={t('contactCreate.type.individual.label')}
            description={t('contactCreate.type.individual.description')}
            selected={contactType === 'individual'}
            onClick={() => setContactType('individual')}
          />
          <TypeOption
            icon={<Building2 size={18} />}
            label={t('contactCreate.type.business.label')}
            description={t('contactCreate.type.business.description')}
            selected={contactType === 'business'}
            onClick={() => setContactType('business')}
          />
        </SimpleGrid>
      ) : (
        <Stack gap="sm">
          <Select {...hint(t('contactCreate.fields.correspondenceLanguage'), t('contactCreate.descriptions.correspondenceLanguage'))}
            data={LANGUAGE_OPTIONS}
            allowDeselect={false}
            required
            {...form.getInputProps('language')}
          />
          {contactType === 'individual' ? (
            <>
              <Select label={t('contactCreate.fields.salutation')} data={translatedSalutationOptions(t)} clearable {...form.getInputProps('salutation')} />
              <Group grow>
                <TextInput label={t('contactCreate.fields.firstName')} required {...form.getInputProps('firstName')} />
                <TextInput label={t('contactCreate.fields.lastName')} required {...form.getInputProps('lastName')} />
              </Group>
              <Group grow>
                <TextInput label={t('contactCreate.fields.birthDate')} type="date" {...form.getInputProps('birthDate')} />
                {/* KAN-32 — chosen from the `country` reference list, not typed. */}
                <Select
                  label={t('contactCreate.fields.nationality')}
                  data={countryOptions}
                  searchable
                  clearable
                  comboboxProps={{ withinPortal: true }}
                  {...form.getInputProps('nationality')}
                />
              </Group>
            </>
          ) : (
            <>
              <TextInput label={t('contactCreate.fields.companyName')} required {...form.getInputProps('companyName')} />
              <Group grow>
                <Select label={t('contactCreate.fields.legalForm')} data={translatedLegalFormOptions(t)} clearable {...form.getInputProps('legalForm')} />
                <TextInput label={t('contactCreate.fields.uid')} placeholder={t('contactCreate.placeholders.uid')} {...form.getInputProps('taxId')} />
              </Group>
            </>
          )}

          <ContactRowGroup
            label={t('contactDetail.contactPoints.phoneNumbers')}
            addLabel={t('contactDetail.contactPoints.addPhone')}
            typeOptions={translatedPhoneTypeOptions(t)}
            defaultType="mobile"
            rows={phones}
            handlers={phoneRowGroup}
            valueLabel={t('contactDetail.contactPoints.phoneValueColumn')}
            renderValueEditor={(value, onChange) => (
              // KAN-460 (option A): the dialog is too narrow for the full country select.
              <PhoneInput compact label={t('contactDetail.phoneInput.country')} value={value} onChange={onChange} />
            )}
            valueEditorMinWidth={COMPACT_PHONE_INPUT_MIN_WIDTH}
            t={t}
          />
          <ContactRowGroup
            label={t('contactDetail.contactPoints.emailAddresses')}
            addLabel={t('contactDetail.contactPoints.addEmail')}
            typeOptions={translatedEmailTypeOptions(t)}
            defaultType="personal"
            rows={emails}
            handlers={emailRowGroup}
            valueLabel={t('contactDetail.contactPoints.emailValueColumn')}
            renderValueEditor={(value, onChange, autoFocus, name) => (
              <TextInput size="xs" type="email" aria-label={name} value={value} autoFocus={autoFocus} onChange={(e) => onChange(e.currentTarget.value)} />
            )}
            // KAN-460: the phone group's width, so both groups share one column template and line up.
            valueEditorMinWidth={COMPACT_PHONE_INPUT_MIN_WIDTH}
            t={t}
          />

          {onOpenExisting && <DuplicateWarningPanel candidates={duplicates} onOpenExisting={onOpenExisting} />}

          <Select label={t('contactCreate.fields.preferredChannel')} data={translatedPreferredChannelOptions(t)} clearable {...form.getInputProps('preferredChannel')} />
          <MultiSelect
            label={t('contactCreate.fields.roles')}
            data={roleOptions}
            value={roles}
            onChange={(next) => setRoles(next as ContactRole[])}
            required
          />
          {holdsCustomer && (
            <Select label={t('contactCreate.fields.lifecycleStatus')} data={translatedLifecycleOptions(t)} {...form.getInputProps('lifecycleStatus')} />
          )}
          <Select label={t('contactCreate.fields.source')} data={translatedSourceOptions(t)} clearable {...form.getInputProps('source')} />
          <Checkbox label={t('contactCreate.fields.hasAddress')} {...form.getInputProps('hasAddress', { type: 'checkbox' })} />
          {form.values.hasAddress && (
            <Stack gap="sm">
              {/* KAN-37: fills the five fields below from a Google Places pick; every field stays editable, line 2 is never touched. */}
              <AddressSearchField
                country={form.values.country || 'CH'}
                onSelect={(address) =>
                  form.setValues({
                    street: address.street,
                    houseNumber: address.houseNumber,
                    postalCode: address.postalCode,
                    locality: address.locality,
                    country: address.country || form.values.country || 'CH',
                  })
                }
              />
              <Group grow>
                <TextInput label={t('contactCreate.fields.street')} {...form.getInputProps('street')} />
                <TextInput label={t('contactCreate.fields.houseNumber')} {...form.getInputProps('houseNumber')} />
              </Group>
              <TextInput label={t('contactCreate.fields.line2')} {...form.getInputProps('line2')} />
              <Group grow>
                <TextInput label={t('contactCreate.fields.postalCode')} {...form.getInputProps('postalCode')} />
                <TextInput label={t('contactCreate.fields.locality')} {...form.getInputProps('locality')} />
              </Group>
              <Select
                label={t('contactCreate.fields.country')}
                data={countryOptions}
                searchable
                allowDeselect={false}
                comboboxProps={{ withinPortal: true }}
                {...form.getInputProps('country')}
              />
            </Stack>
          )}
          <Checkbox label={t('contactCreate.fields.marketingConsent')} {...form.getInputProps('marketingConsent', { type: 'checkbox' })} />
        </Stack>
      )}
    </Wizard>
  )
}

interface RowGroupHandlers {
  onCreate: (draft: { type: string; value: string }) => Promise<void>
  onUpdate: (id: string, patch: RepeatableRowPatch) => Promise<void>
  onDelete: (id: string) => Promise<void>
}

/**
 * The in-memory equivalent of the detail screen's PATCH-backed handlers:
 * a freshly added row of a type with no current primary becomes the
 * primary, and marking a new primary clears the previous one of the same
 * type in the same update — the exact ADR-067 invariant
 * `RepeatableRowGroup` delegates to its caller.
 */
function controlledRowGroup(setRows: Dispatch<SetStateAction<RepeatableRowValue[]>>): RowGroupHandlers {
  return {
    onCreate: async ({ type, value }) => {
      setRows((prev) => [
        ...prev,
        {
          id: crypto.randomUUID(),
          type,
          value,
          label: null,
          isPrimary: !prev.some((r) => r.type === type),
          consentGranted: false,
        },
      ])
    },
    onUpdate: async (id, patch) => {
      setRows((prev) => {
        const merged = prev.map((r) => (r.id === id ? { ...r, ...patch } : r))
        if (patch.isPrimary) {
          const target = merged.find((r) => r.id === id)
          if (target) return merged.map((r) => (r.type === target.type ? { ...r, isPrimary: r.id === id } : r))
        }
        return merged
      })
    },
    onDelete: async (id) => {
      setRows((prev) => prev.filter((r) => r.id !== id))
    },
  }
}

function ContactRowGroup({
  label,
  valueLabel,
  addLabel,
  typeOptions,
  defaultType,
  rows,
  handlers,
  renderValueEditor,
  valueEditorMinWidth,
  t,
}: {
  label: string
  addLabel: string
  typeOptions: { value: string; label: string }[]
  defaultType: string
  rows: RepeatableRowValue[]
  handlers: RowGroupHandlers
  valueLabel: string
  renderValueEditor: (value: string, onChange: (v: string) => void, autoFocus: boolean, accessibleName: string) => ReactNode
  valueEditorMinWidth?: number
  t: (key: string, options?: Record<string, unknown>) => string
}) {
  return (
    <Stack gap="xs">
      <Text size="sm" fw={600}>
        {label}
      </Text>
      <RepeatableRowGroup
        mode="create"
        label={label}
        valueLabel={valueLabel}
        addLabel={addLabel}
        formerLabel={t('contactDetail.contactPoints.former')}
        typeOptions={typeOptions}
        defaultType={defaultType}
        rows={rows}
        renderValueEditor={renderValueEditor}
        valueEditorMinWidth={valueEditorMinWidth}
        onCreate={handlers.onCreate}
        onUpdate={handlers.onUpdate}
        onDelete={handlers.onDelete}
        labels={contactPointRowLabels(t)}
      />
    </Stack>
  )
}

function TypeOption({
  icon,
  label,
  description,
  selected,
  onClick,
}: {
  icon: ReactNode
  label: string
  description: string
  selected: boolean
  onClick: () => void
}) {
  return (
    <UnstyledButton
      onClick={onClick}
      style={{
        border: `1.5px solid ${selected ? purple[6] : slate[3]}`,
        borderRadius: 10,
        padding: 16,
        backgroundColor: selected ? purple[0] : undefined,
      }}
    >
      <Stack gap={4}>
        <Group gap="xs">
          {icon}
          <Text fw={600}>{label}</Text>
        </Group>
        <Text size="sm" c="dimmed">
          {description}
        </Text>
      </Stack>
    </UnstyledButton>
  )
}
