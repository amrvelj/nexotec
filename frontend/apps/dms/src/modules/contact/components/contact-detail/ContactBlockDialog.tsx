import { useEffect, useState } from 'react'
import { Stack, Text, Textarea } from '@mantine/core'
import { useTranslation } from 'react-i18next'
import { FormDialog } from '@nexotec/ui-kit'
import { api, ApiError } from '../../../../api/client'
import { useIdempotencyKey } from '../../../../hooks/useIdempotencyKey'
import type { ContactRead } from '../../../../api/types'
import { apiRefusalText } from '../../../../utils/apiRefusal'

/**
 * The contact's two blocks, each placed with a reason and lifted without one:
 * - `credit` — the credit block (KAN-44 / ADR-065, S-D19): stops a contract
 *   from being confirmed, never an offer; `POST /v1/contacts/{id}/credit-block`.
 * - `contact` — «Gesperrt» (KAN-810; Anto, 2026-11-26): a workshop order
 *   refuses the contact as its orderer or invoice recipient;
 *   `POST /v1/contacts/{id}/block`.
 *
 * The ONLY place the SPA calls either endpoint. The reason is checked here
 * too, so the user is told before the round trip (the API says 409 without
 * one). Reached from the shared row menu (`buildContactRowMenu`).
 */
export type ContactBlockKind = 'credit' | 'contact'

const KIND = {
  credit: { path: 'credit-block', ns: 'creditBlockDialog', isBlocked: (c: BlockedContact) => c.creditBlock },
  contact: { path: 'block', ns: 'contactBlockDialog', isBlocked: (c: BlockedContact) => c.blocked },
} as const

type BlockedContact = Pick<ContactRead, 'id' | 'version' | 'contactNumber' | 'creditBlock' | 'blocked'>

interface ContactBlockDialogProps {
  kind: ContactBlockKind
  opened: boolean
  onClose: () => void
  contact: BlockedContact
  onSaved: (updated: ContactRead) => void
}

export function ContactBlockDialog({ kind, opened, onClose, contact, onSaved }: ContactBlockDialogProps) {
  const { t } = useTranslation()
  const { path, ns, isBlocked: readBlocked } = KIND[kind]
  const isBlocked = readBlocked(contact)
  const [reason, setReason] = useState('')
  const [reasonError, setReasonError] = useState<string | null>(null)
  const [submitError, setSubmitError] = useState<string | null>(null)
  const [submitting, setSubmitting] = useState(false)
  // KAN-266: a retried submit replays the first answer instead of a 409.
  const idempotency = useIdempotencyKey()

  useEffect(() => {
    if (opened) {
      setReason('')
      setReasonError(null)
      setSubmitError(null)
      idempotency.renew()
    }
  }, [opened, idempotency])

  const submit = async () => {
    if (!isBlocked && reason.trim().length === 0) {
      setReasonError(t(`${ns}.reasonRequired`))
      return
    }
    setSubmitting(true)
    setSubmitError(null)
    try {
      const updated = await api.post<ContactRead>(
        `/contacts/${contact.id}/${path}`,
        { blocked: !isBlocked, reason: isBlocked ? null : reason.trim() },
        { 'If-Match': String(contact.version), ...idempotency.headers() },
      )
      idempotency.renew()
      onSaved(updated)
      onClose()
    } catch (err) {
      // A 409 that names its reason says it; a bare one is the version check.
      const named = err instanceof ApiError && typeof err.details?.reason === 'string'
      setSubmitError(
        err instanceof ApiError && err.status === 409 && !named ? t(`${ns}.conflict`) : apiRefusalText(t, err, `${ns}.failed`),
      )
    } finally {
      setSubmitting(false)
    }
  }

  return (
    <FormDialog
      opened={opened}
      onClose={onClose}
      title={isBlocked ? t(`${ns}.clearTitle`) : t(`${ns}.setTitle`)}
      onSubmit={submit}
      submitLabel={isBlocked ? t(`${ns}.clearSubmit`) : t(`${ns}.setSubmit`)}
      cancelLabel={t(`${ns}.cancel`)}
      submitting={submitting}
    >
      <Stack gap="sm">
        {isBlocked ? (
          <Text size="sm">{t(`${ns}.clearBody`, { number: contact.contactNumber })}</Text>
        ) : (
          <>
            <Text size="sm">{t(`${ns}.setBody`)}</Text>
            <Textarea
              label={t(`${ns}.reasonLabel`)}
              value={reason}
              onChange={(e) => {
                setReason(e.currentTarget.value)
                if (reasonError) setReasonError(null)
              }}
              error={reasonError}
              autosize
              minRows={2}
              maxRows={5}
              maxLength={500}
              withAsterisk
              data-autofocus
            />
          </>
        )}
        {submitError && (
          <Text size="xs" c="red">
            {submitError}
          </Text>
        )}
      </Stack>
    </FormDialog>
  )
}
