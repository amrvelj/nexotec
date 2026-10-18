import { useState, type FormEvent } from 'react'
import { Link } from 'react-router-dom'
import { Alert, Anchor, Button, Stack, Text, TextInput } from '@mantine/core'
import { useTranslation } from 'react-i18next'
import { api, ApiError } from '../api/client'
import { AuthScreen } from './auth/AuthScreen'

/** KAN-196 (FR-AU-14/16): asks for the address and says the same thing
 * whether or not an account exists — the mail goes out only where there is
 * someone to send it to. */
export function PasswordResetRequestPage() {
  const { t } = useTranslation()
  const [email, setEmail] = useState('')
  const [sent, setSent] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)

  const submit = async (event: FormEvent) => {
    event.preventDefault()
    setBusy(true)
    setError(null)
    try {
      await api.post('/auth/password-reset/request', { email })
      setSent(true)
    } catch (err) {
      setError(err instanceof ApiError ? err.message : t('common.requestFailed'))
    } finally {
      setBusy(false)
    }
  }

  return (
    <AuthScreen title={t('auth.reset.title')} hint={t('auth.reset.hint')}>
      {sent ? (
        <Stack gap="md">
          <Alert color="green">
            {t('auth.reset.sent')}
          </Alert>
          <Text ta="center" fz="sm">
            <Anchor component={Link} to="/login">
              {t('auth.reset.backToLogin')}
            </Anchor>
          </Text>
        </Stack>
      ) : (
        <form onSubmit={submit} noValidate>
          <Stack gap="md">
            <TextInput
              label={t('auth.reset.email')}
              type="email"
              value={email}
              onChange={(e) => setEmail(e.currentTarget.value)}
              autoComplete="username"
              autoFocus
              required
            />
            {error && (
              <Alert color="red" role="alert">
                {error}
              </Alert>
            )}
            <Button type="submit" loading={busy} fullWidth>
              {t('auth.reset.submit')}
            </Button>
            <Text ta="center" fz="sm">
              <Anchor component={Link} to="/login">
                {t('auth.reset.backToLogin')}
              </Anchor>
            </Text>
          </Stack>
        </form>
      )}
    </AuthScreen>
  )
}
