import { useState } from 'react'
import { TextInput } from '@mantine/core'
import { useTranslation } from 'react-i18next'
import { FormDialog } from '@nexotec/ui-kit'
import { api } from '../../api/client'
import type { VehicleMdmCreateResult, VehicleMdmRead } from '../../api/types'

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

  const reset = () => {
    setVin('')
    setExisting(null)
  }

  const submit = async () => {
    setSubmitting(true)
    setExisting(null)
    try {
      const result = await api.post<VehicleMdmCreateResult>('/vehicle-mdm', { vin })
      if (result.created) {
        reset()
        onCreated(result.vehicle)
      } else {
        // FR-V-15 — not an error: offer to open the existing record.
        setExisting(result.vehicle)
      }
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
    </FormDialog>
  )
}
