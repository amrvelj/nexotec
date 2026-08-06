import { useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { Alert, Button, Center, Paper, PasswordInput, Stack, TextInput, Title } from '@mantine/core'
import { useForm } from '@mantine/form'
import { useAuth } from '../auth/AuthContext'
import { isApiError } from '../api/isApiError'

export function LoginPage() {
  const { login } = useAuth()
  const navigate = useNavigate()
  const [error, setError] = useState<string | null>(null)
  const [submitting, setSubmitting] = useState(false)

  const form = useForm({
    initialValues: { email: '', password: '' },
  })

  const handleSubmit = form.onSubmit(async (values) => {
    setError(null)
    setSubmitting(true)
    try {
      await login(values.email, values.password)
      navigate('/customers')
    } catch (err) {
      setError(isApiError(err) ? err.message : 'Login failed.')
    } finally {
      setSubmitting(false)
    }
  })

  return (
    <Center h="100vh">
      <Paper withBorder shadow="sm" p="xl" w={360}>
        <Stack gap="md">
          <Title order={3}>DMS Platform login</Title>
          <form onSubmit={handleSubmit}>
            <Stack gap="sm">
              {error && (
                <Alert color="red" title="Could not log in">
                  {error}
                </Alert>
              )}
              <TextInput
                label="Email"
                type="email"
                required
                {...form.getInputProps('email')}
              />
              <PasswordInput label="Password" required {...form.getInputProps('password')} />
              <Button type="submit" loading={submitting} fullWidth>
                Log in
              </Button>
            </Stack>
          </form>
        </Stack>
      </Paper>
    </Center>
  )
}
