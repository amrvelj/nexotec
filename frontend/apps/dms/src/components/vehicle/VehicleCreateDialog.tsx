import { useEffect, useState } from 'react'
import { Alert, TextInput } from '@mantine/core'
import { useTranslation } from 'react-i18next'
import { FormDialog } from '@nexotec/ui-kit'
import { api, ApiError } from '../../api/client'
import type { VehicleMdmCreateResult, VehicleMdmRead } from '../../api/types'
import { useIdempotencyKey } from '../../hooks/useIdempotencyKey'

interface VehicleCreateDialogProps {
  opened: boolean
  onClose: () => void
  onCreated: (vehicle: VehicleMdmRead) => void
}

/**
 * FR-V-02/FR-V-03 create, and FR-V-15's "a VIN that already exists is not
 * a validation error" in the same dialog: the API always answers 200 with
 * a real record, `created` says which case it was. This is also the
 * shared-form contract's create half — the same field set (today just
 * VIN; PR-6's catalogue browse eventually adds the FzKey link) is what an
 * inline VehicleMdm identity edit uses on the detail screen's Identity tab.
 */
export function VehicleCreateDialog({ opened, onClose, onCreated }: VehicleCreateDialogProps) {
  const { t } = useTranslation()
  const [vin, setVin] = useState('')
  const [submitting, setSubmitting] = useState(false)
  const [existing, setExisting] = useState<VehicleMdmRead | null>(null)
  const [error, setError] = useState<string | null>(null)
  // KAN-266: one key per submission — a retry after a failure gets the
  // first answer back; another VIN, or another opening, gets a new key.
  const idempotency = useIdempotencyKey()
  useEffect(() => {
    if (opened) idempotency.renew()
  }, [opened, idempotency])

  const reset = () => {
    setVin('')
    setExisting(null)
    setError(null)
  }

  const submit = async () => {
    setSubmitting(true)
    setExisting(null)
    setError(null)
    try {
      const body = { vin }
      const result = await api.post<VehicleMdmCreateResult>('/vehicle-mdm', body, idempotency.headers(body))
      idempotency.renew()
      if (result.created) {
        reset()
        onCreated(result.vehicle)
      } else {
        // FR-V-15 — not an error: offer to open the existing record.
        setExisting(result.vehicle)
      }
    } catch (err) {
      // Shown, so the user can send it again: a retry carries the same key.
      setError(err instanceof ApiError ? err.message : t('vehicleCreate.error'))
    } finally {
      setSubmitting(false)
    }
  }

  return (
    <FormDialog
      opened={opened}
      onClose={() => {
        reset()
        onClose()
      }}
      title={t('vehicleCreate.title')}
      onSubmit={submit}
      submitLabel={t('vehicleCreate.submit')}
      cancelLabel={t('common.cancel')}
      submitting={submitting}
      submitDisabled={vin.length !== 17}
      existingRecordNotice={
        existing
          ? {
              message: t('vehicleCreate.existingNotice', { vehicleNumber: existing.vehicleNumber }),
              openLabel: t('vehicleCreate.openExisting'),
              onOpenExisting: () => {
                reset()
                onCreated(existing)
              },
            }
          : null
      }
    >
      <TextInput
        label={t('vehicleCreate.vinLabel')}
        value={vin}
        onChange={(e) => setVin(e.currentTarget.value.toUpperCase())}
        maxLength={17}
        data-autofocus
        styles={{ input: { fontFamily: 'monospace' } }}
      />
      {error && (
        <Alert color="red" role="alert">
          {error}
        </Alert>
      )}
    </FormDialog>
  )
}
