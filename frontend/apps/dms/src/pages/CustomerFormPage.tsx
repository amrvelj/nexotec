import { useEffect, useState } from 'react'
import { useNavigate, useParams } from 'react-router-dom'
import { Alert, Button, Checkbox, Container, Group, List, Loader, Select, Stack, Text, TextInput, Title } from '@mantine/core'
import { useForm } from '@mantine/form'
import { useSetBreadcrumb } from '@nexotec/ui-kit'
import { api, ApiError } from '../api/client'
import { LANGUAGE_OPTIONS, LEGAL_FORM_OPTIONS, LIFECYCLE_OPTIONS, SALUTATION_OPTIONS, SOURCE_OPTIONS } from '../customerOptions'
import type {
  CustomerEmailPage,
  CustomerLifecycleStatus,
  CustomerPhonePage,
  CustomerRead,
  CustomerSource,
  CustomerType,
  Language,
  LegalForm,
  Salutation,
} from '../api/types'

interface FormValues {
  language: Language
  salutation: Salutation | ''
  firstName: string
  lastName: string
  birthDate: string
  nationality: string
  companyName: string
  legalForm: LegalForm | ''
  lifecycleStatus: CustomerLifecycleStatus
  source: CustomerSource | ''
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
  lifecycleStatus: 'prospect',
  source: '',
  hasAddress: false,
  street: '',
  houseNumber: '',
  postalCode: '',
  locality: '',
}

// Editing, not creating — creation is the two-step CustomerCreateFlow
// wizard (FR-03), routed separately at /customers/new. This screen only
// ever loads an existing customer by :id. customer_type is immutable
// (FR-03) so it's read from the loaded customer, never chosen here — but
// it still has to gate which fields render/submit, same individual vs.
// business split CustomerCreateFlow uses, or a business customer's PATCH
// would try to send empty first_name/last_name and fail CustomerUpdate's
// individual-only-fields validation.
export function CustomerFormPage() {
  const { id } = useParams<{ id: string }>()
  const navigate = useNavigate()

  const [loading, setLoading] = useState(true)
  const [submitting, setSubmitting] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [version, setVersion] = useState<number | null>(null)
  const [customerType, setCustomerType] = useState<CustomerType>('individual')
  // Contact points are managed on separate endpoints per customer (FR-07) —
  // shown read-only here for now. Add/remove/set-primary is a follow-up
  // screen, not part of this Phase B contract fix.
  const [existingPhones, setExistingPhones] = useState<CustomerPhonePage['items']>([])
  const [existingEmails, setExistingEmails] = useState<CustomerEmailPage['items']>([])
  const [customerLabel, setCustomerLabel] = useState<string | null>(null)

  useSetBreadcrumb(['Master Data', 'Customers', customerLabel ?? 'Edit customer'])

  const form = useForm<FormValues>({ initialValues: EMPTY_VALUES })

  useEffect(() => {
    if (!id) return
    Promise.all([
      api.get<CustomerRead>(`/customers/${id}`),
      api.get<CustomerPhonePage>(`/customers/${id}/phones`),
      api.get<CustomerEmailPage>(`/customers/${id}/emails`),
    ])
      .then(([c, phones, emails]) => {
        setVersion(c.version)
        setCustomerType(c.customerType)
        setExistingPhones(phones.items)
        setExistingEmails(emails.items)
        setCustomerLabel(c.customerType === 'business' ? c.companyName : `${c.firstName} ${c.lastName}`)
        form.setValues({
          language: c.language,
          salutation: c.salutation ?? '',
          firstName: c.firstName ?? '',
          lastName: c.lastName ?? '',
          birthDate: c.birthDate ?? '',
          nationality: c.nationality ?? '',
          companyName: c.companyName ?? '',
          legalForm: c.legalForm ?? '',
          lifecycleStatus: c.lifecycleStatus,
          source: c.source ?? '',
          hasAddress: c.address !== null,
          street: c.address?.street ?? '',
          houseNumber: c.address?.houseNumber ?? '',
          postalCode: c.address?.postalCode ?? '',
          locality: c.address?.locality ?? '',
        })
      })
      .catch((err) => setError(err instanceof ApiError ? err.message : 'Failed to load customer.'))
      .finally(() => setLoading(false))
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [id])

  const handleSubmit = form.onSubmit(async (values) => {
    if (!id) return
    setError(null)
    setSubmitting(true)

    const address = values.hasAddress
      ? {
          street: values.street,
          houseNumber: values.houseNumber,
          postalCode: values.postalCode,
          locality: values.locality,
          country: 'CH',
        }
      : null

    try {
      // Contact points aren't part of PATCH — see FR-07 note above.
      const payload = {
        language: values.language,
        lifecycleStatus: values.lifecycleStatus,
        source: values.source || null,
        address,
        ...(customerType === 'individual'
          ? { salutation: values.salutation || null, firstName: values.firstName, lastName: values.lastName, birthDate: values.birthDate || null, nationality: values.nationality || null }
          : { companyName: values.companyName, legalForm: values.legalForm || null }),
      }
      await api.patch(`/customers/${id}`, payload, { 'If-Match': String(version) })
      navigate('/customers')
    } catch (err) {
      setError(err instanceof ApiError ? err.message : 'Failed to save customer.')
    } finally {
      setSubmitting(false)
    }
  })

  if (loading) return <Loader />

  return (
    <Container py="xl" size="sm">
      <Stack gap="md">
        <Title order={2}>Edit customer</Title>
        <form onSubmit={handleSubmit}>
          <Stack gap="sm">
            {error && <Alert color="red">{error}</Alert>}
            <Select
              label="Correspondence language"
              description="How this customer's documents and letters are written — not the same as your own UI language."
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
                <Select label="Legal form" data={LEGAL_FORM_OPTIONS} clearable {...form.getInputProps('legalForm')} />
              </>
            )}
            <Stack gap={4}>
              <Text size="sm" fw={600}>
                Contact points
              </Text>
              <List size="sm" spacing={2}>
                {existingPhones.map((p) => (
                  <List.Item key={p.id}>
                    {p.phoneE164} ({p.phoneType}
                    {p.isPrimary ? ', primary' : ''})
                  </List.Item>
                ))}
                {existingEmails.map((e) => (
                  <List.Item key={e.id}>
                    {e.emailAddress} ({e.emailType}
                    {e.isPrimary ? ', primary' : ''})
                  </List.Item>
                ))}
                {existingPhones.length === 0 && existingEmails.length === 0 && <List.Item>None on file.</List.Item>}
              </List>
              <Text size="xs" c="dimmed">
                Adding, editing or removing contact points isn't available on this screen yet.
              </Text>
            </Stack>
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
            <Group justify="flex-end" mt="md">
              <Button variant="default" onClick={() => navigate('/customers')} type="button">
                Cancel
              </Button>
              <Button type="submit" loading={submitting}>
                Save
              </Button>
            </Group>
          </Stack>
        </form>
      </Stack>
    </Container>
  )
}
