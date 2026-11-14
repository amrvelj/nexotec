import { useEffect, useState } from 'react'
import { Stack, Text, TextInput } from '@mantine/core'
import { FormDialog } from '@nexotec/ui-kit'
import { useTranslation } from 'react-i18next'
import { api } from '../api/client'
import type { IntegrationConnectionRead } from '../api/types'
import { settableSecretSlots } from './connectionRowMenu'
import { apiRefusalText } from '../utils/apiRefusal'

/**
 * «Secret setzen/erneuern» for one connection: each slot the provider
 * requires, write-only (a stored value is never read back). Opened from the
 * dealer card and the platform grid's row menu alike (KAN-650).
 */
export function RotateSecretModal({
  connection,
  requiredSecretSlots,
  onClose,
  onRotated,
}: {
  connection: IntegrationConnectionRead | null
  requiredSecretSlots: readonly string[]
  onClose: () => void
  onRotated: () => void
}) {
  const { t } = useTranslation()
  const [values, setValues] = useState<Record<string, string>>({})
  const [submitting, setSubmitting] = useState(false)
  const [error, setError] = useState<string | null>(null)
  // KAN-607 — the slots this attempt replaced before a later one was refused,
  // and the slot that was refused.
  const [savedBeforeRefusal, setSavedBeforeRefusal] = useState<string[]>([])
  const [refusedSlot, setRefusedSlot] = useState<string | null>(null)
  // The modal stays mounted for every connection: closing it, or opening it
  // for another connection, starts clean — no refusal, no typed value from
  // an earlier attempt.
  useEffect(() => {
    setValues({})
    setError(null)
    setSavedBeforeRefusal([])
    setRefusedSlot(null)
  }, [connection?.id])

  if (!connection) return null

  const slotLabel = (slot: string) => t(`integrationEnums.secretSlot.${slot}`, slot)
  // KAN-603 — every slot the provider requires, set or not, so a connection
  // whose first secret write failed can be given it here.
  const slots = settableSecretSlots(connection, requiredSecretSlots)
  const stored = new Set<string>((connection.secretSlots ?? []).map((s) => s.slot))
  const nothingStored = stored.size === 0

  const submit = async () => {
    setSubmitting(true)
    setError(null)
    setSavedBeforeRefusal([])
    setRefusedSlot(null)
    const saved: string[] = []
    let current: string | null = null
    try {
      for (const slot of slots) {
        const value = values[slot]
        if (!value) continue
        current = slot
        await api.put(`/integrations/connections/${connection.id}/secrets/${slot}`, { secretValue: value })
        saved.push(slot)
      }
      onRotated()
      onClose()
    } catch (err) {
      // KAN-607 — a refused write is said in the UI language, the dialog stays
      // open with what was typed, and the slots already replaced are named:
      // their rotation happened even though a later one was refused.
      setError(apiRefusalText(t, err, 'integrationsList.rotateError'))
      setSavedBeforeRefusal(saved)
      setRefusedSlot(current)
      if (saved.length > 0) onRotated()
    } finally {
      setSubmitting(false)
    }
  }

  return (
    <FormDialog
      opened
      onClose={onClose}
      title={t(nothingStored ? 'integrationsList.rotateModal.titleSet' : 'integrationsList.rotateModal.title', {
        name: connection.displayName,
      })}
      onSubmit={submit}
      submitLabel={t(nothingStored ? 'integrationsList.actions.setSecret' : 'integrationsList.actions.rotateSecret')}
      cancelLabel={t('common.cancel')}
      submitting={submitting}
    >
      <Stack gap="sm">
        {error && (
          <Stack gap={4} role="alert">
            {/* With slots already saved, the refusal is said as the refused
                slot's: its sentence («nothing was saved») is about that slot. */}
            {savedBeforeRefusal.length > 0 && (
              <Text size="sm">
                {t('integrationsList.rotateModal.savedBeforeRefusal', {
                  slots: savedBeforeRefusal.map(slotLabel).join(', '),
                  refused: refusedSlot ? slotLabel(refusedSlot) : '—',
                })}
              </Text>
            )}
            <Text size="sm" c="red">{error}</Text>
          </Stack>
        )}
        <Text size="xs" c="dimmed">{t('integrationsList.rotateModal.hint')}</Text>
        {slots.map((slot) => (
          <TextInput
            key={slot}
            label={slotLabel(slot)}
            type="password"
            placeholder={stored.has(slot) ? undefined : t('integrationsList.rotateModal.notSet')}
            value={values[slot] ?? ''}
            onChange={(e) => {
              // KAN-249 — read before the updater (see ConnectDialog).
              const value = e.currentTarget.value
              setValues((prev) => ({ ...prev, [slot]: value }))
            }}
          />
        ))}
      </Stack>
    </FormDialog>
  )
}
