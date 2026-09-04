import { useEffect, useMemo, useRef, useState, type Dispatch, type ReactNode, type SetStateAction } from 'react'
import { Checkbox, Group, Select, SimpleGrid, Stack, Text, TextInput, UnstyledButton } from '@mantine/core'
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
import { api, ApiError } from '../api/client'
import {
  LANGUAGE_OPTIONS,
  LEGAL_FORM_OPTIONS,
  LIFECYCLE_OPTIONS,
  PREFERRED_CHANNEL_OPTIONS,
  SALUTATION_OPTIONS,
  SOURCE_OPTIONS,
  translatedEmailTypeOptions,
  translatedPhoneTypeOptions,
} from '../customerOptions'
import { DuplicateWarningPanel } from './DuplicateWarningPanel'
import { PhoneInput } from './PhoneInput'
import type {
  CustomerCreateInput,
  CustomerDuplicateCandidate,
  CustomerDuplicateCandidateList,
  CustomerLifecycleStatus,
  CustomerRead,
  CustomerSource,
  CustomerType,
  EmailType,
  Language,
  LegalForm,
  PhoneType,
  PreferredChannel,
  Salutation,
} from '../api/types'

const STEPS: WizardStep[] = [
  { id: 'type', label: 'Type' },
  { id: 'details', label: 'Details' },
]

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
  lifecycleStatus: CustomerLifecycleStatus
  source: CustomerSource | ''
  marketingConsent: boolean
  hasAddress: boolean
  street: string
  line2: string
  houseNumber: string
  postalCode: string
  locality: string
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
}

export interface CustomerCreateFlowProps {
  onSuccess: (customer: CustomerRead) => void;
  onCancel: () => void;
  /** Pre-selects a type and skips straight to step 2 — e.g. an "Add
   * business customer" entry point elsewhere in the app. */
  initialCustomerType?: CustomerType;
  /** FR-04: "shows matches in a side panel with an 'open this customer
   * instead' action." Omit to hide the "Open instead" affordance (e.g.
   * an embedding context with nowhere sensible to navigate to). */
  onOpenExisting?: (customerId: string) => void;
}

/**
 * FR-03: "Two-step form. Step 1 chooses individual or business, which is
 * then immutable. Step 2 shows only the fields valid for [type]."
 *
 * This is the reusable container, not a page — it owns the wizard state,
 * validation, and the actual POST /customers call, but takes onSuccess /
 * onCancel callbacks instead of navigating itself and renders no page-level
 * chrome (no Container/Title). That's deliberate: the "Add New Customer"
 * feature note says this process "should be reusable in different parts of
 * the DMS" — CustomerCreatePage renders this full-page for /customers/new,
 * and the same component can later be dropped into a Modal from e.g. a
 * future Offer-creation screen with different onSuccess/onCancel wiring,
 * with zero duplication of the two-step logic, validation, or the API call.
 */
export function CustomerCreateFlow({ onSuccess, onCancel, initialCustomerType, onOpenExisting }: CustomerCreateFlowProps) {
  const { t } = useTranslation()
  const [step, setStep] = useState(initialCustomerType ? 1 : 0)
  const [customerType, setCustomerType] = useState<CustomerType>(initialCustomerType ?? 'individual')
  // § ADR-067 — the same RepeatableRowGroup the detail screen uses, in
  // `create` mode: one primary per type-group, marking a new primary
  // unmarks the old in the same interaction, consent on the row. Held in
  // memory (no customer exists yet) and flushed with the create POST; the
  // per-mutation callbacks are the local-array equivalent of the detail
  // screen's PATCH calls.
  const [phones, setPhones] = useState<RepeatableRowValue[]>([])
  const [emails, setEmails] = useState<RepeatableRowValue[]>([])
  const phoneRowGroup = useMemo(() => controlledRowGroup(setPhones), [])
  const emailRowGroup = useMemo(() => controlledRowGroup(setEmails), [])
  const [submitting, setSubmitting] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [duplicates, setDuplicates] = useState<CustomerDuplicateCandidate[]>([])

  // Reused across retries of the same logical submit attempt (e.g. a
  // network error and a re-click of Submit) so the backend's idempotency
  // check — POST /customers accepts an Idempotency-Key header — collapses
  // them into one customer instead of creating duplicates.
  const idempotencyKey = useRef(crypto.randomUUID())

  const form = useForm<FormValues>({ initialValues: EMPTY_VALUES })

  // FR-04: "While the user types name, email or phone in the create form,
  // the system queries a duplicate-check endpoint (debounced, per
  // keystroke)... never a blocking gate." The endpoint takes a single `q`
  // matched broadly server-side against name/company/email/phone, so one
  // debounced query is enough — priority order (name, then email, then
  // phone) picks whichever field is most likely to be the one the user is
  // currently filling in, per US-02's own example ("while I type a new
  // customer's name").
  const dedupeSignal =
    (customerType === 'individual' ? form.values.lastName : form.values.companyName).trim() ||
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
      .get<CustomerDuplicateCandidateList>(`/customers/duplicate-check?q=${encodeURIComponent(debouncedDedupeSignal)}`)
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

    if (customerType === 'individual' && (!values.firstName.trim() || !values.lastName.trim())) {
      setError('First name and last name are required for an individual customer.')
      return
    }
    if (customerType === 'business' && !values.companyName.trim()) {
      setError('Company name is required for a business customer.')
      return
    }
    const hasContactPoint = phones.some((p) => p.value) || emails.some((e) => e.value)
    if (!hasContactPoint) {
      setError('At least one phone number or email address is required.')
      return
    }

    setSubmitting(true)
    try {
      const payload: CustomerCreateInput = {
        customerType,
        language: values.language,
        salutation: values.salutation || null,
        preferredChannel: values.preferredChannel || null,
        phones: phones
          .filter((p) => p.value.trim())
          .map((p) => ({ phoneType: p.type as PhoneType, phoneE164: p.value, isPrimary: p.isPrimary, label: p.label ?? null })),
        emails: emails
          .filter((e) => e.value.trim())
          .map((e) => ({ emailType: e.type as EmailType, emailAddress: e.value, isPrimary: e.isPrimary, label: e.label ?? null })),
        // KAN-30: CustomerCreate expects addresses: CustomerAddressCreate[]
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
                addressCountry: 'CH',
                isPrimary: true,
              },
            ]
          : [],
        lifecycleStatus: values.lifecycleStatus,
        source: values.source || null,
        marketingConsent: values.marketingConsent,
        ...(customerType === 'individual'
          ? { firstName: values.firstName, lastName: values.lastName, birthDate: values.birthDate || null, nationality: values.nationality || null }
          : { companyName: values.companyName, legalForm: values.legalForm || null, taxId: values.taxId || null }),
      }
      const created = await api.post<CustomerRead>('/customers', payload, { 'Idempotency-Key': idempotencyKey.current })
      onSuccess(created)
    } catch (err) {
      setError(err instanceof ApiError ? err.message : 'Failed to create customer.')
    } finally {
      setSubmitting(false)
    }
  }

  return (
    <Wizard
      steps={STEPS}
      activeIndex={step}
      onBack={() => setStep(0)}
      onNext={handleNext}
      onCancel={onCancel}
      submitLabel="Create customer"
      submitting={submitting}
      error={error}
    >
      {step === 0 ? (
        <SimpleGrid cols={2} spacing="md">
          <TypeOption
            icon={<User size={18} />}
            label="Individual"
            description="A private person — name, date of birth, nationality."
            selected={customerType === 'individual'}
            onClick={() => setCustomerType('individual')}
          />
          <TypeOption
            icon={<Building2 size={18} />}
            label="Business"
            description="A company — legal name, legal form, UID."
            selected={customerType === 'business'}
            onClick={() => setCustomerType('business')}
          />
        </SimpleGrid>
      ) : (
        <Stack gap="sm">
          <Select
            label="Correspondence language"
            description="How this customer's documents and letters are written."
            data={LANGUAGE_OPTIONS}
            allowDeselect={false}
            required
            {...form.getInputProps('language')}
          />
          {customerType === 'individual' ? (
            <>
              <Select label="Salutation" data={SALUTATION_OPTIONS} clearable {...form.getInputProps('salutation')} />
              <Group grow>
                <TextInput label="First name" required {...form.getInputProps('firstName')} />
                <TextInput label="Last name" required {...form.getInputProps('lastName')} />
              </Group>
              <Group grow>
                <TextInput label="Date of birth" type="date" {...form.getInputProps('birthDate')} />
                <TextInput label="Nationality" placeholder="CH" maxLength={2} {...form.getInputProps('nationality')} />
              </Group>
            </>
          ) : (
            <>
              <TextInput label="Company name" required {...form.getInputProps('companyName')} />
              <Group grow>
                <Select label="Legal form" data={LEGAL_FORM_OPTIONS} clearable {...form.getInputProps('legalForm')} />
                <TextInput label="UID" placeholder="CHE-123.456.789" {...form.getInputProps('taxId')} />
              </Group>
            </>
          )}

          <ContactRowGroup
            label={t('customerDetail.contactPoints.phoneNumbers')}
            addLabel={t('customerDetail.contactPoints.addPhone')}
            typeOptions={translatedPhoneTypeOptions(t)}
            defaultType="mobile"
            rows={phones}
            handlers={phoneRowGroup}
            renderValueEditor={(value, onChange) => (
              <PhoneInput label={t('customerDetail.phoneInput.country')} value={value} onChange={onChange} />
            )}
            t={t}
          />
          <ContactRowGroup
            label={t('customerDetail.contactPoints.emailAddresses')}
            addLabel={t('customerDetail.contactPoints.addEmail')}
            typeOptions={translatedEmailTypeOptions(t)}
            defaultType="personal"
            rows={emails}
            handlers={emailRowGroup}
            renderValueEditor={(value, onChange, autoFocus) => (
              <TextInput size="xs" type="email" value={value} autoFocus={autoFocus} onChange={(e) => onChange(e.currentTarget.value)} />
            )}
            t={t}
          />

          {onOpenExisting && <DuplicateWarningPanel candidates={duplicates} onOpenExisting={onOpenExisting} />}

          <Select label="Preferred contact channel" data={PREFERRED_CHANNEL_OPTIONS} clearable {...form.getInputProps('preferredChannel')} />
          <Select label="Lifecycle status" data={LIFECYCLE_OPTIONS} {...form.getInputProps('lifecycleStatus')} />
          <Select label="Source" data={SOURCE_OPTIONS} clearable {...form.getInputProps('source')} />
          <Checkbox label="Has address" {...form.getInputProps('hasAddress', { type: 'checkbox' })} />
          {form.values.hasAddress && (
            <Stack gap="sm">
              <Group grow>
                <TextInput label="Street" {...form.getInputProps('street')} />
                <TextInput label="House number" {...form.getInputProps('houseNumber')} />
              </Group>
              <TextInput label="Address line 2 (c/o, PO box, ...)" {...form.getInputProps('line2')} />
              <Group grow>
                <TextInput label="Postal code" {...form.getInputProps('postalCode')} />
                <TextInput label="Locality" {...form.getInputProps('locality')} />
              </Group>
            </Stack>
          )}
          <Checkbox label="Marketing consent" {...form.getInputProps('marketingConsent', { type: 'checkbox' })} />
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
  addLabel,
  typeOptions,
  defaultType,
  rows,
  handlers,
  renderValueEditor,
  t,
}: {
  label: string
  addLabel: string
  typeOptions: { value: string; label: string }[]
  defaultType: string
  rows: RepeatableRowValue[]
  handlers: RowGroupHandlers
  renderValueEditor: (value: string, onChange: (v: string) => void, autoFocus: boolean) => ReactNode
  t: (key: string) => string
}) {
  return (
    <Stack gap="xs">
      <Text size="sm" fw={600}>
        {label}
      </Text>
      <RepeatableRowGroup
        mode="create"
        label={label}
        addLabel={addLabel}
        formerLabel={t('customerDetail.contactPoints.former')}
        typeOptions={typeOptions}
        defaultType={defaultType}
        rows={rows}
        renderValueEditor={renderValueEditor}
        onCreate={handlers.onCreate}
        onUpdate={handlers.onUpdate}
        onDelete={handlers.onDelete}
        labels={{
          primary: t('customerDetail.contactPoints.primary'),
          noLongerValid: t('customerDetail.contactPoints.noLongerValid'),
          doesNotWork: t('customerDetail.contactPoints.doesNotWork'),
          doesNotWorkReasonPlaceholder: t('customerDetail.contactPoints.doesNotWorkReasonPlaceholder'),
          delete: t('customerDetail.contactPoints.delete'),
          consent: t('customerDetail.contactPoints.consent'),
          save: t('customerDetail.contactPoints.save'),
          cancel: t('customerDetail.contactPoints.cancel'),
          confirm: t('customerDetail.contactPoints.confirm'),
          none: t('customerDetail.contactPoints.none'),
          labelFieldPlaceholder: t('customerDetail.contactPoints.labelPlaceholder'),
        }}
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
