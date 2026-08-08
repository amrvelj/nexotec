import { useRef, useState, type ReactNode } from 'react'
import { Checkbox, Group, Select, SimpleGrid, Stack, Text, TextInput, UnstyledButton } from '@mantine/core'
import { useForm } from '@mantine/form'
import { Building2, User } from 'lucide-react'
import { Wizard, purple, slate, type WizardStep } from '@nexotec/ui-kit'
import { api, ApiError } from '../api/client'
import {
  EMAIL_TYPE_OPTIONS,
  LANGUAGE_OPTIONS,
  LEGAL_FORM_OPTIONS,
  LIFECYCLE_OPTIONS,
  PHONE_TYPE_OPTIONS,
  PREFERRED_CHANNEL_OPTIONS,
  SALUTATION_OPTIONS,
  SOURCE_OPTIONS,
} from '../customerOptions'
import { ContactListInput, type ContactRow } from './ContactListInput'
import { PhoneInput } from './PhoneInput'
import type {
  CustomerCreateInput,
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
export function CustomerCreateFlow({ onSuccess, onCancel, initialCustomerType }: CustomerCreateFlowProps) {
  const [step, setStep] = useState(initialCustomerType ? 1 : 0)
  const [customerType, setCustomerType] = useState<CustomerType>(initialCustomerType ?? 'individual')
  const [phones, setPhones] = useState<ContactRow<PhoneType>[]>([])
  const [emails, setEmails] = useState<ContactRow<EmailType>[]>([{ key: crypto.randomUUID(), type: 'private', value: '', isPrimary: true }])
  const [submitting, setSubmitting] = useState(false)
  const [error, setError] = useState<string | null>(null)

  // Reused across retries of the same logical submit attempt (e.g. a
  // network error and a re-click of Submit) so the backend's idempotency
  // check — POST /customers accepts an Idempotency-Key header — collapses
  // them into one customer instead of creating duplicates.
  const idempotencyKey = useRef(crypto.randomUUID())

  const form = useForm<FormValues>({ initialValues: EMPTY_VALUES })

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
        phones: phones.filter((p) => p.value).map((p) => ({ phoneType: p.type, phoneE164: p.value, isPrimary: p.isPrimary })),
        emails: emails.filter((e) => e.value).map((e) => ({ emailType: e.type, emailAddress: e.value, isPrimary: e.isPrimary })),
        address: values.hasAddress
          ? { street: values.street, houseNumber: values.houseNumber, postalCode: values.postalCode, locality: values.locality, country: 'CH' }
          : null,
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
            icon={<User size={20} />}
            label="Individual"
            description="A private person — name, date of birth, nationality."
            selected={customerType === 'individual'}
            onClick={() => setCustomerType('individual')}
          />
          <TypeOption
            icon={<Building2 size={20} />}
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

          <ContactListInput
            label="Phone numbers"
            addLabel="Add phone"
            typeOptions={PHONE_TYPE_OPTIONS}
            rows={phones}
            onChange={setPhones}
            newRowType="mobile"
            renderValue={(value, onValueChange) => <PhoneInput label="Country" value={value} onChange={onValueChange} />}
          />
          <ContactListInput
            label="Email addresses"
            addLabel="Add email"
            typeOptions={EMAIL_TYPE_OPTIONS}
            rows={emails}
            onChange={setEmails}
            newRowType="private"
            renderValue={(value, onValueChange) => (
              <TextInput label="Address" type="email" value={value} onChange={(e) => onValueChange(e.currentTarget.value)} />
            )}
          />

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
