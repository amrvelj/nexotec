import { useState, type FormEvent } from 'react'
import { Link, useNavigate, useSearchParams } from 'react-router-dom'
import { Alert, Anchor, Button, PasswordInput, Stack, Text } from '@mantine/core'
import { useTranslation } from 'react-i18next'
import { api } from '../api/client'
import type { LoginStepResponse } from '../api/types'
import { useAuth } from '../auth/AuthContext'
import { ACCOUNT_SECURITY_PATH } from '../components/ProtectedRoute'
import { AuthScreen } from './auth/AuthScreen'
import { passwordErrorMessage } from './auth/passwordErrors'

/** KAN-196 (FR-AU-13): the invited person sets their own password through
 * the link in the invitation mail and is signed in — straight into MFA
 * enrolment where the Betrieb requires it. */
export function InvitationAcceptPage() {
  const { t } = useTranslation()
  const navigate = useNavigate()
  const { completeLogin } = useAuth()
  const [params] = useSearchParams()
  const token = params.get('token') ?? ''
  const [password, setPassword] = useState('')
  const [error, setError] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)
  const [mfaNext, setMfaNext] = useState(false)

  const submit = async (event: FormEvent) => {
    event.preventDefault()
    setBusy(true)
    setError(null)
    try {
      const step = await api.post<LoginStepResponse>('/auth/invitations/accept', { token, password })
      if (step.session) {
        completeLogin(step.session)
        navigate(step.session.mfaEnrolmentRequired ? ACCOUNT_SECURITY_PATH : '/', { replace: true })
      } else {
        // A re-issued invitation to a user who already holds a second factor:
        // the password is set; the sign-in itself goes through the login
        // screen's MFA step, like every other sign-in.
        setMfaNext(true)
      }
    } catch (err) {
      setError(passwordErrorMessage(err, 'auth.invitation.invalidLink'))
    } finally {
      setBusy(false)
    }
  }

  if (mfaNext) {
    return (
      <AuthScreen title={t('auth.invitation.title')}>
        <Alert color="green">{t('auth.invitation.mfaNext')}</Alert>
        <Text ta="center" fz="sm">
          <Anchor component={Link} to="/login">
            {t('auth.reset.backToLogin')}
          </Anchor>
        </Text>
      </AuthScreen>
    )
  }

  if (!token) {
    return (
      <AuthScreen title={t('auth.invitation.title')}>
        <Alert color="red" role="alert">
          {t('auth.invitation.invalidLink')}
        </Alert>
        <Text ta="center" fz="sm">
          <Anchor component={Link} to="/login">
            {t('auth.reset.backToLogin')}
          </Anchor>
        </Text>
      </AuthScreen>
    )
  }

  return (
    <AuthScreen title={t('auth.invitation.title')} hint={t('auth.invitation.hint')}>
      <form onSubmit={submit} noValidate>
        <Stack gap="md">
          <PasswordInput
            label={t('auth.invitation.newPassword')}
            description={t('auth.password.rules')}
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
            {t('auth.invitation.submit')}
          </Button>
        </Stack>
      </form>
    </AuthScreen>
  )
}
