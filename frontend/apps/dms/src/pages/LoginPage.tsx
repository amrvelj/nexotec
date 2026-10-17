import { useState, type FormEvent } from 'react'
import { Link, Navigate } from 'react-router-dom'
import { Alert, Anchor, Button, Group, PasswordInput, Stack, Text, TextInput } from '@mantine/core'
import { useTranslation } from 'react-i18next'
import { api, ApiError } from '../api/client'
import type { LoginResponse, LoginStepResponse, PasskeyOptionsResponse } from '../api/types'
import { useAuth } from '../auth/AuthContext'
import { assertPasskey, passkeysSupported } from '../auth/webauthn'
import { ACCOUNT_SECURITY_PATH } from '../components/ProtectedRoute'
import { AuthScreen } from './auth/AuthScreen'

type Step = { kind: 'credentials' } | { kind: 'mfa'; challengeId: string; factors: string[] }

/**
 * KAN-196 (D-A-07): Nexotec's own sign-in. E-mail and password first; when
 * the account holds a second factor the server answers with a challenge and
 * this screen asks for the authenticator code, a recovery code or a passkey.
 * Every refusal reads the same (FR-AU-16) — the screen never says whether
 * the address exists.
 */
export function LoginPage() {
  const { t } = useTranslation()
  const { user, loading, mfaEnrolmentRequired, login, completeLogin } = useAuth()
  const [email, setEmail] = useState('')
  const [password, setPassword] = useState('')
  const [code, setCode] = useState('')
  const [useRecovery, setUseRecovery] = useState(false)
  const [step, setStep] = useState<Step>({ kind: 'credentials' })
  const [error, setError] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)

  // A signed-in visitor — and this screen's own successful sign-in, once the
  // session is applied — goes to the dashboard, or to enrolment first when
  // the session was minted without a required second factor (D-A-36).
  if (!loading && user) return <Navigate to={mfaEnrolmentRequired ? ACCOUNT_SECURITY_PATH : '/'} replace />

  const land = (session: LoginResponse) => completeLogin(session)

  const failure = (err: unknown, uniform: string) => {
    if (err instanceof ApiError && err.status === 401 && err.details?.step === 'expired') return t('auth.login.stepExpired')
    if (err instanceof ApiError && err.status === 401) return uniform
    if (err instanceof ApiError) return err.message
    return t('common.requestFailed')
  }

  const submitCredentials = async (event: FormEvent) => {
    event.preventDefault()
    setBusy(true)
    setError(null)
    try {
      const result: LoginStepResponse = await login(email, password)
      if (result.session) {
        land(result.session)
      } else if (result.mfaRequired && result.challengeId) {
        setStep({ kind: 'mfa', challengeId: result.challengeId, factors: result.factors ?? [] })
        setPassword('')
      }
    } catch (err) {
      setError(failure(err, t('auth.login.failed')))
    } finally {
      setBusy(false)
    }
  }

  const submitCode = async (event: FormEvent) => {
    event.preventDefault()
    if (step.kind !== 'mfa') return
    setBusy(true)
    setError(null)
    try {
      const result = await api.post<LoginStepResponse>('/auth/mfa/verify', { challengeId: step.challengeId, code })
      if (result.session) land(result.session)
    } catch (err) {
      setError(failure(err, t('auth.login.codeInvalid')))
    } finally {
      setBusy(false)
    }
  }

  const signInWithPasskey = async () => {
    if (step.kind !== 'mfa') return
    setBusy(true)
    setError(null)
    try {
      const options = await api.post<PasskeyOptionsResponse>('/auth/mfa/passkey/options', {
        challengeId: step.challengeId,
      })
      const credential = await assertPasskey(options.options)
      const result = await api.post<LoginStepResponse>('/auth/mfa/passkey/verify', {
        challengeId: step.challengeId,
        credential,
      })
      if (result.session) land(result.session)
    } catch (err) {
      setError(err instanceof ApiError ? failure(err, t('auth.login.passkeyFailed')) : t('auth.login.passkeyFailed'))
    } finally {
      setBusy(false)
    }
  }

  if (step.kind === 'mfa') {
    const offersPasskey = step.factors.includes('passkey') && passkeysSupported()
    const offersTotp = step.factors.includes('totp')
    return (
      <AuthScreen title={t('auth.login.mfaTitle')} hint={t('auth.login.mfaHint')}>
        <form onSubmit={submitCode} noValidate>
          <Stack gap="md">
            <TextInput
              label={useRecovery || !offersTotp ? t('auth.login.recoveryCode') : t('auth.login.code')}
              value={code}
              onChange={(e) => setCode(e.currentTarget.value)}
              autoComplete="one-time-code"
              inputMode={useRecovery || !offersTotp ? 'text' : 'numeric'}
              autoFocus
              required
            />
            {error && (
              <Alert color="red" role="alert">
                {error}
              </Alert>
            )}
            <Button type="submit" loading={busy} fullWidth>
              {t('auth.login.verify')}
            </Button>
            <Group justify="space-between">
              {offersTotp && (
                <Anchor component="button" type="button" fz="sm" onClick={() => setUseRecovery((v) => !v)}>
                  {useRecovery ? t('auth.login.useAuthenticator') : t('auth.login.useRecoveryCode')}
                </Anchor>
              )}
              {offersPasskey && (
                <Anchor component="button" type="button" fz="sm" onClick={() => void signInWithPasskey()}>
                  {t('auth.login.usePasskey')}
                </Anchor>
              )}
            </Group>
            <Anchor
              component="button"
              type="button"
              fz="sm"
              c="dimmed"
              onClick={() => {
                setStep({ kind: 'credentials' })
                setCode('')
                setError(null)
              }}
            >
              {t('auth.login.backToLogin')}
            </Anchor>
          </Stack>
        </form>
      </AuthScreen>
    )
  }

  return (
    <AuthScreen title={t('auth.login.title')} hint={t('auth.login.subtitle')}>
      <form onSubmit={submitCredentials} noValidate>
        <Stack gap="md">
          <TextInput
            label={t('auth.login.email')}
            type="email"
            value={email}
            onChange={(e) => setEmail(e.currentTarget.value)}
            autoComplete="username"
            autoFocus
            required
          />
          <PasswordInput
            label={t('auth.login.password')}
            value={password}
            onChange={(e) => setPassword(e.currentTarget.value)}
            autoComplete="current-password"
            required
          />
          {error && (
            <Alert color="red" role="alert">
              {error}
            </Alert>
          )}
          <Button type="submit" loading={busy} fullWidth>
            {t('auth.login.submit')}
          </Button>
          <Text ta="center" fz="sm">
            <Anchor component={Link} to="/password-reset">
              {t('auth.login.forgotPassword')}
            </Anchor>
          </Text>
        </Stack>
      </form>
    </AuthScreen>
  )
}
