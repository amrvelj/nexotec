import { useState, type FormEvent } from 'react'
import { Link, useSearchParams } from 'react-router-dom'
import { Alert, Anchor, Button, PasswordInput, Stack, Text } from '@mantine/core'
import { useTranslation } from 'react-i18next'
import { api } from '../api/client'
import { AuthScreen } from './auth/AuthScreen'
import { passwordErrorMessage } from './auth/passwordErrors'

/** KAN-196 (FR-AU-14): the link from the reset mail lands here with its
 * single-use token; the new password is set and every session ended. */
export function PasswordResetConfirmPage() {
  const { t } = useTranslation()
  const [params] = useSearchParams()
  const token = params.get('token') ?? ''
  const [password, setPassword] = useState('')
  const [done, setDone] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)

  const submit = async (event: FormEvent) => {
    event.preventDefault()
    setBusy(true)
    setError(null)
    try {
      await api.post('/auth/password-reset/confirm', { token, password })
      setDone(true)
    } catch (err) {
      setError(passwordErrorMessage(err))
    } finally {
      setBusy(false)
    }
  }

  const backToLogin = (
    <Text ta="center" fz="sm">
      <Anchor component={Link} to="/login">
        {t('auth.reset.backToLogin')}
      </Anchor>
    </Text>
  )

  if (!token) {
    return (
      <AuthScreen title={t('auth.reset.confirmTitle')}>
        <Alert color="red" role="alert">
          {t('auth.reset.invalidLink')}
        </Alert>
        {backToLogin}
      </AuthScreen>
    )
  }

  return (
    <AuthScreen title={t('auth.reset.confirmTitle')} hint={done ? undefined : t('auth.password.rules')}>
      {done ? (
        <Stack gap="md">
          <Alert color="green">
            {t('auth.reset.done')}
          </Alert>
          {backToLogin}
        </Stack>
      ) : (
        <form onSubmit={submit} noValidate>
          <Stack gap="md">
            <PasswordInput
              label={t('auth.reset.newPassword')}
              value={password}
              onChange={(e) => setPassword(e.currentTarget.value)}
              autoComplete="new-password"
              autoFocus
              required
            />
            {error && (
              <Alert color="red" role="alert">
                {error}
              </Alert>
            )}
            <Button type="submit" loading={busy} fullWidth>
              {t('auth.reset.confirmSubmit')}
            </Button>
            {backToLogin}
          </Stack>
        </form>
      )}
    </AuthScreen>
  )
}
