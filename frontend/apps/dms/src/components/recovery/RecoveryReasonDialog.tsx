import { useEffect, useState } from 'react'
import { Alert, Stack, Text, Textarea, TextInput } from '@mantine/core'
import { FormDialog } from '@nexotec/ui-kit'
import { useTranslation } from 'react-i18next'
import { api, ApiError } from '../../api/client'
import type { ChangeEmailRequest, GrantManagerRequest, RecoveryRequest, RecoveryResult, RecoveryUserRead } from '../../api/types'
import { useIdempotencyKey } from '../../hooks/useIdempotencyKey'
import { userDisplayName } from '../../userOptions'
import type { RecoveryChoice } from './recoveryRowMenu'

// The API's own limit (`REASON_MAX_LENGTH`).
const REASON_MAX = 500
// A plausible address before the request goes out (the API's EmailStr is
// the judge); without it a typo came back as an untranslated 422.
const EMAIL_SHAPE = /^[^\s@]+@[^\s@]+\.[^\s@]+$/

export interface RecoveryReasonDialogProps {
  opened: boolean
  onClose: () => void
  user: RecoveryUserRead | null
  choice: RecoveryChoice | null
  onDone: (result: RecoveryResult) => void
}

/**
 * KAN-202 — one dialog for every recovery action (FR-AU-25): what the action
 * does, what the user and the Betrieb will see, and the stated reason it
 * cannot go without. The Betrieb's manager can read the reason (Anto,
 * 2026-10-22); the user is not notified yet (KAN-216) and the dialog says
 * so. A reset of the second factor is the one irreversible action: the
 * destructive tone. The two actions that write the user row (grant-manager,
 * change-email) send the version the search read (If-Match).
 */
export function RecoveryReasonDialog({ opened, onClose, user, choice, onDone }: RecoveryReasonDialogProps) {
  const { t } = useTranslation()
  const [reason, setReason] = useState('')
  const [email, setEmail] = useState('')
  const [submitting, setSubmitting] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const idempotency = useIdempotencyKey()

  useEffect(() => {
    if (!opened) return
    setReason('')
    setEmail('')
    setError(null)
    idempotency.renew()
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [opened, user?.id, choice])

  const changingEmail = choice === 'change-email'
  const canSubmit = reason.trim().length > 0 && (!changingEmail || email.trim().length > 0) && !submitting

  const submit = async () => {
    if (!user || !choice) return
    if (choice === 'change-email' && !EMAIL_SHAPE.test(email.trim())) {
      setError(t('recovery.errors.invalid_email'))
      return
    }
    setSubmitting(true)
    setError(null)
    try {
      let result: RecoveryResult
      const ifMatch = { 'If-Match': String(user.version) }
      if (choice === 'grant-manager') {
        const body: GrantManagerRequest = { userId: user.id, reason: reason.trim() }
        result = await api.post<RecoveryResult>(`/platform/dealerships/${user.dealershipId}/recovery/grant-manager`, body, {
          ...idempotency.headers(body),
          ...ifMatch,
        })
      } else if (choice === 'change-email') {
        const body: ChangeEmailRequest = { email: email.trim(), reason: reason.trim() }
        result = await api.post<RecoveryResult>(`/platform/users/${user.id}/recovery/change-email`, body, {
          ...idempotency.headers(body),
          ...ifMatch,
        })
      } else {
        const body: RecoveryRequest = { reason: reason.trim() }
        result = await api.post<RecoveryResult>(`/platform/users/${user.id}/recovery/${choice}`, body, idempotency.headers(body))
      }
      idempotency.renew()
      onDone(result)
    } catch (err) {
      setError(recoveryErrorText(t, err))
    } finally {
      setSubmitting(false)
    }
  }

  const name = user ? userDisplayName(user) : ''
  const key = choice ?? 'revoke-sessions'
  const values = { name, email: user?.email ?? '', betrieb: user?.dealershipName ?? '' }
  const notice =
    key === 'reissue-invitation' ? 'recovery.dialog.noticeByMail' : key === 'change-email' ? 'recovery.dialog.noticeChangeEmail' : 'recovery.dialog.notice'
  return (
    <FormDialog
      opened={opened}
      onClose={onClose}
      title={t(`recovery.actions.${key}.title`, values)}
      destructive={choice === 'reset-mfa'}
      onSubmit={submit}
      submitLabel={t(`recovery.actions.${key}.confirm`)}
      cancelLabel={t('common.cancel')}
      submitting={submitting}
      submitDisabled={!canSubmit}
    >
      <Stack gap="md">
        <Text size="sm">{t(`recovery.actions.${key}.effect`, values)}</Text>
        <Text size="sm" c="dimmed">
          {t(notice)}
        </Text>
        {error && (
          <Alert color="red" role="alert">
            {error}
          </Alert>
        )}
        {changingEmail && (
          <TextInput
            label={t('recovery.dialog.newEmail')}
            type="email"
            value={email}
            onChange={(e) => setEmail(e.currentTarget.value)}
            required
            data-autofocus
          />
        )}
        <Textarea
          label={t('recovery.dialog.reason')}
          description={t('recovery.dialog.reasonHint')}
          value={reason}
          onChange={(e) => setReason(e.currentTarget.value)}
          maxLength={REASON_MAX}
          autosize
          minRows={3}
          required
          data-autofocus={changingEmail ? undefined : true}
        />
      </Stack>
    </FormDialog>
  )
}

type T = (key: string | string[], options?: Record<string, unknown>) => string

/** The API's refusal in the UI language — by its stable `rule`; a missing
 * account and a version that moved on are said as such; the API's own
 * sentence only for a refusal the bundles do not know. */
function recoveryErrorText(t: T, err: unknown): string {
  if (err instanceof ApiError) {
    const rule = typeof err.details?.rule === 'string' ? err.details.rule : null
    if (rule) return t([`recovery.errors.${rule}`, 'recovery.errors.generic'], { message: err.message })
    if (err.status === 403) return t('recovery.errors.forbidden')
    if (err.status === 404) return t('recovery.errors.notFound')
    if (err.status === 409 && err.details && 'currentVersion' in err.details) return t('recovery.errors.conflict')
    if (err.status === 400 || err.status === 422) return t('recovery.errors.invalid')
    return t('recovery.errors.generic', { message: err.message })
  }
  return t('common.requestFailed')
}
