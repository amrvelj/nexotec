import { useEffect, useState } from 'react'
import { Alert, Stack, Text, Textarea } from '@mantine/core'
import { FormDialog } from '@nexotec/ui-kit'
import { useTranslation } from 'react-i18next'
import { api, ApiError } from '../../api/client'
import type { GrantManagerRequest, RecoveryRequest, RecoveryResult, RecoveryUserRead } from '../../api/types'
import { useIdempotencyKey } from '../../hooks/useIdempotencyKey'
import { userDisplayName } from '../../userOptions'
import type { RecoveryChoice } from './recoveryRowMenu'

// The API's own limit (`REASON_MAX_LENGTH`).
const REASON_MAX = 500

export interface RecoveryReasonDialogProps {
  opened: boolean
  onClose: () => void
  user: RecoveryUserRead | null
  choice: RecoveryChoice | null
  onDone: (result: RecoveryResult) => void
}

/**
 * KAN-202 — one dialog for every recovery action (FR-AU-25): what the action
 * does, who will see it, and the stated reason it cannot go without. The
 * reason is read by the Betrieb's manager in its sign-in history (Anto,
 * 2026-10-22), so the dialog says so before staff type it. A reset of the
 * second factor is the one irreversible action: the destructive tone.
 */
export function RecoveryReasonDialog({ opened, onClose, user, choice, onDone }: RecoveryReasonDialogProps) {
  const { t } = useTranslation()
  const [reason, setReason] = useState('')
  const [submitting, setSubmitting] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const idempotency = useIdempotencyKey()

  useEffect(() => {
    if (!opened) return
    setReason('')
    setError(null)
    idempotency.renew()
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [opened, user?.id, choice])

  const submit = async () => {
    if (!user || !choice) return
    setSubmitting(true)
    setError(null)
    try {
      let result: RecoveryResult
      if (choice === 'grant-manager') {
        const body: GrantManagerRequest = { userId: user.id, reason: reason.trim() }
        result = await api.post<RecoveryResult>(
          `/platform/dealerships/${user.dealershipId}/recovery/grant-manager`,
          body,
          idempotency.headers(body),
        )
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
  return (
    <FormDialog
      opened={opened}
      onClose={onClose}
      title={t(`recovery.actions.${key}.title`, { name, betrieb: user?.dealershipName ?? '' })}
      destructive={choice === 'reset-mfa'}
      onSubmit={submit}
      submitLabel={t(`recovery.actions.${key}.confirm`)}
      cancelLabel={t('common.cancel')}
      submitting={submitting}
      submitDisabled={reason.trim().length === 0 || submitting}
    >
      <Stack gap="md">
        <Text size="sm">{t(`recovery.actions.${key}.effect`, { name, email: user?.email ?? '', betrieb: user?.dealershipName ?? '' })}</Text>
        <Text size="sm" c="dimmed">
          {key === 'reissue-invitation' ? t('recovery.dialog.noticeByMail') : t('recovery.dialog.notice')}
        </Text>
        {error && (
          <Alert color="red" role="alert">
            {error}
          </Alert>
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
          data-autofocus
        />
      </Stack>
    </FormDialog>
  )
}

type T = (key: string | string[], options?: Record<string, unknown>) => string

/** The API's refusal in the UI language — by its stable `rule`, falling
 * back to the API's own sentence for one the bundles do not know. */
function recoveryErrorText(t: T, err: unknown): string {
  if (err instanceof ApiError) {
    const rule = typeof err.details?.rule === 'string' ? err.details.rule : null
    if (rule) return t([`recovery.errors.${rule}`, 'recovery.errors.generic'], { message: err.message })
    if (err.status === 403) return t('recovery.errors.forbidden')
    return err.message
  }
  return t('common.requestFailed')
}
