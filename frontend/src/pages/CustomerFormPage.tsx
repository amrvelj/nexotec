import { useEffect, useState } from 'react'
import { useNavigate, useParams } from 'react-router-dom'
import {
  Alert,
  Button,
  Checkbox,
  Container,
  Group,
  Loader,
  Select,
  Stack,
  TextInput,
  Title,
} from '@mantine/core'
import { useForm } from '@mantine/form'
import { api, ApiError } from '../api/client'
import { PhoneInput } from '../components/PhoneInput'
import type { CustomerLifecycleStatus, CustomerRead, CustomerSource } from '../api/types'

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

  const form = useForm<FormValues>({ initialValues: EMPTY_VALUES })

  useEffect(() => {
    if (!id) return
    api
      .get<CustomerRead>(`/customers/${id}`)
      .then((c) => {
        setVersion(c.version)
        form.setValues({
          firstName: c.firstName,
          lastName: c.lastName,
          email: c.email ?? '',
          phone: c.phone ?? '',
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
    if (!values.email && !values.phone) {
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

    const payload = {
      firstName: values.firstName,
      lastName: values.lastName,
      email: values.email || null,
      phone: values.phone || null,
      lifecycleStatus: values.lifecycleStatus,
      source: values.source || null,
      address,
    }

    try {
      if (isEdit && id) {
        await api.patch(`/customers/${id}`, payload, { 'If-Match': String(version) })
      } else {
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
            <TextInput label="First name" required {...form.getInputProps('firstName')} />
            <TextInput label="Last name" required {...form.getInputProps('lastName')} />
            <TextInput label="Email" type="email" {...form.getInputProps('email')} />
            <PhoneInput
              label="Phone"
              value={form.values.phone}
              onChange={(next) => form.setFieldValue('phone', next)}
            />
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
