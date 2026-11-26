import { useEffect, useState } from 'react'
import { Text, TextInput } from '@mantine/core'
import { useTranslation } from 'react-i18next'
import { FormDialog } from '@nexotec/ui-kit'
import { RefusalAlert } from './RefusalAlert'

/** A job's name, for «Gruppe hinzufügen» and «Umbenennen». `onSave` throws on a refusal, which is shown here. */
export function JobNameDialog({
  opened,
  title,
  submitLabel,
  initialName,
  onClose,
  onSave,
  onReload,
}: {
  opened: boolean
  title: string
  submitLabel: string
  initialName: string
  onClose: () => void
  onSave: (name: string) => Promise<void>
  onReload: () => void
}) {
  const { t } = useTranslation()
  const [name, setName] = useState(initialName)
  const [submitting, setSubmitting] = useState(false)
  const [error, setError] = useState<unknown>(null)

  useEffect(() => {
    if (opened) {
      setName(initialName)
      setError(null)
    }
  }, [opened, initialName])

  const submit = async () => {
    setSubmitting(true)
    setError(null)
    try {
      await onSave(name.trim())
    } catch (err) {
      setError(err)
    } finally {
      setSubmitting(false)
    }
  }

  return (
    <FormDialog
      opened={opened}
      onClose={onClose}
      title={title}
      onSubmit={submit}
      submitLabel={submitLabel}
      cancelLabel={t('common.cancel')}
      submitting={submitting}
      // A mirror of the server's `service_order_job_name_required`.
      submitDisabled={name.trim() === ''}
    >
      <TextInput
        label={t('serviceOrder.jobs.dialog.name')}
        value={name}
        onChange={(e) => setName(e.currentTarget.value)}
        maxLength={200}
        required
        data-autofocus
      />
      {error !== null && (
        <RefusalAlert
          error={error}
          fallbackKey="serviceOrder.saveError"
          onReload={() => {
            onReload()
            onClose()
          }}
        />
      )}
    </FormDialog>
  )
}

/** «Löschen?» for a job or a position: the server decides whether it may go; a refusal is said here. */
export function ConfirmDeleteDialog({
  opened,
  title,
  body,
  onClose,
  onConfirm,
  onReload,
}: {
  opened: boolean
  title: string
  body: string
  onClose: () => void
  onConfirm: () => Promise<void>
  onReload: () => void
}) {
  const { t } = useTranslation()
  const [submitting, setSubmitting] = useState(false)
  const [error, setError] = useState<unknown>(null)

  useEffect(() => {
    if (opened) setError(null)
  }, [opened])

  const submit = async () => {
    setSubmitting(true)
    setError(null)
    try {
      await onConfirm()
    } catch (err) {
      setError(err)
    } finally {
      setSubmitting(false)
    }
  }

  return (
    <FormDialog
      opened={opened}
      onClose={onClose}
      title={title}
      onSubmit={submit}
      submitLabel={t('serviceOrder.positions.confirmDelete')}
      cancelLabel={t('common.cancel')}
      submitting={submitting}
      destructive
    >
      <Text size="sm">{body}</Text>
      {error !== null && (
        <RefusalAlert
          error={error}
          fallbackKey="serviceOrder.saveError"
          onReload={() => {
            onReload()
            onClose()
          }}
        />
      )}
    </FormDialog>
  )
}
