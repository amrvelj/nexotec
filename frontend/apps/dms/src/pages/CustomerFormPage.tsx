import { useEffect, useState } from 'react'
import { useNavigate, useParams } from 'react-router-dom'
import {
  Alert,
  Button,
  Checkbox,
  Container,
  Group,
  List,
  Loader,
  Select,
  Stack,
  Text,
  TextInput,
  Title,
} from '@mantine/core'
import { useForm } from '@mantine/form'
import { useSetBreadcrumb } from '@nexotec/ui-kit'
import { api, ApiError } from '../api/client'
import { PhoneInput } from '../components/PhoneInput'
import type {
  CustomerEmailPage,
  CustomerLifecycleStatus,
  CustomerPhonePage,
  CustomerRead,
  CustomerSource,
  Language,
} from '../api/types'

const LANGUAGE_OPTIONS: { value: Language; label: string }[] = [
  { value: 'de', label: 'Deutsch' },
  { value: 'fr', label: 'Français' },
  { value: 'it', label: 'Italiano' },
  { value: 'en', label: 'English' },
]

const LIFECYCLE_OPTIONS: { value: CustomerLifecycleStatus; label: string }[] = [
  { value: 'prospect', label: 'Prospect' },
  { value: 'active', label: 'Active' },
  { value: 'inactive', label: 'Inactive' },
  { value: 'do_not_contact', label: 'Do not contact' },
]

const SOURCE_OPTIONS: { value: CustomerSource; label: string }[] = [
  { value: 'walk_in', label: 'Walk-in' },
  { value: 'phone', label: 'Phone' },
  { value: 'web_lead', label: 'Web lead' },
  { value: 'marketplace', label: 'Marketplace' },
  { value: 'other', label: 'Other' },
]

interface FormValues {
  language: Language
  firstName: string
  lastName: string
  email: string
  phone: string
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
  firstName: '',
  lastName: '',
  email: '',
  phone: '',
  lifecycleStatus: 'prospect',
  source: '',
  hasAddress: false,
  street: '',
  houseNumber: '',
  postalCode: '',
  locality: '',
}

export function CustomerFormPage() {
  const { id } = useParams<{ id: string }>()
  const isEdit = Boolean(id)
  const navigate = useNavigate()

  const [loading, setLoading] = useState(isEdit)
  const [submitting, setSubmitting] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [version, setVersion] = useState<number | null>(null)
  // Contact points are managed on separate endpoints per customer (FR-07) —
  // shown read-only here for now. Add/remove/set-primary is a follow-up
  // screen, not part of this Phase B contract fix.
  const [existingPhones, setExistingPhones] = useState<CustomerPhonePage['items']>([])
  const [existingEmails, setExistingEmails] = useState<CustomerEmailPage['items']>([])
  const [customerLabel, setCustomerLabel] = useState<string | null>(null)

  useSetBreadcrumb(['Master Data', 'Customers', isEdit ? (customerLabel ?? 'Edit customer') : 'New customer'])

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
        setExistingPhones(phones.items)
        setExistingEmails(emails.items)
        setCustomerLabel(c.customerType === 'business' ? c.companyName : `${c.firstName} ${c.lastName}`)
        form.setValues({
          language: c.language,
          firstName: c.firstName ?? '',
          lastName: c.lastName ?? '',
          email: '',
          phone: '',
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
    if (!isEdit && !values.email && !values.phone) {
      setError('At least one of email or phone is required.')
      return
    }

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
      if (isEdit && id) {
        // Contact points aren't part of PATCH — see FR-07 note above.
        const payload = {
          language: values.language,
          firstName: values.firstName,
          lastName: values.lastName,
          lifecycleStatus: values.lifecycleStatus,
          source: values.source || null,
          address,
        }
        await api.patch(`/customers/${id}`, payload, { 'If-Match': String(version) })
      } else {
        const payload = {
          customerType: 'individual' as const,
          language: values.language,
          firstName: values.firstName,
          lastName: values.lastName,
          lifecycleStatus: values.lifecycleStatus,
          source: values.source || null,
          address,
          emails: values.email
            ? [{ emailType: 'private' as const, emailAddress: values.email, isPrimary: true }]
            : [],
          phones: values.phone
            ? [{ phoneType: 'mobile' as const, phoneE164: values.phone, isPrimary: true }]
            : [],
        }
        await api.post('/customers', payload)
      }
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
        <Title order={2}>{isEdit ? 'Edit customer' : 'New customer'}</Title>
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
            <TextInput label="First name" required {...form.getInputProps('firstName')} />
            <TextInput label="Last name" required {...form.getInputProps('lastName')} />
            {isEdit ? (
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
                  {existingPhones.length === 0 && existingEmails.length === 0 && (
                    <List.Item>None on file.</List.Item>
                  )}
                </List>
                <Text size="xs" c="dimmed">
                  Adding, editing or removing contact points isn't available on this screen yet.
                </Text>
              </Stack>
            ) : (
              <>
                <TextInput label="Email" type="email" {...form.getInputProps('email')} />
                <PhoneInput
                  label="Phone"
                  value={form.values.phone}
                  onChange={(next) => form.setFieldValue('phone', next)}
                />
              </>
            )}
            <Select
              label="Lifecycle status"
              data={LIFECYCLE_OPTIONS}
              {...form.getInputProps('lifecycleStatus')}
            />
            <Select
              label="Source"
              data={SOURCE_OPTIONS}
              clearable
              {...form.getInputProps('source')}
            />
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
