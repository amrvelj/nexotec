import { useEffect, useState } from 'react'
import { Group, Stack, Text, TextInput, Textarea } from '@mantine/core'
import { useQueryClient } from '@tanstack/react-query'
import { useTranslation } from 'react-i18next'
import { FormDialog } from '@nexotec/ui-kit'
import { api } from '../../api/client'
import { useFieldHint } from '../../hooks/useFieldHint'
import { useIdempotencyKey } from '../../hooks/useIdempotencyKey'
import type { StockItemRead } from '../../api/types'
import { apiRefusalText } from '../../utils/apiRefusal'
import { isChassisNumber, normalisedVin, normalisedVinOrChassisNumber } from '../../utils/vin'

/** The API's limit on the reason (`StockItemVinCorrectionRequest.reason`). */
const REASON_MAX = 500

interface VinCorrectionDialogProps {
  opened: boolean
  onClose: () => void
  item: Pick<StockItemRead, 'id' | 'version' | 'stockNumber' | 'vin' | 'vehicleId' | 'configurationId'>
  /** The VIN the field opens with — the configurator hands over the one the
   * user typed there; the row menu opens it empty. */
  initialVin?: string
  /** After the correction, with the stock item the API answered. The dialog
   * has already updated the caches; it closes itself afterwards. */
  onCorrected?: (updated: StockItemRead) => void
}

/**
 * KAN-557 (ADR-116 (2)-(3)) — «VIN korrigieren»: the only place the SPA calls
 * `POST /v1/inventory/stock-items/{id}/vin-correction` (`stock_vin_correction`,
 * the dealer manager; If-Match; an Idempotency-Key per opening). One
 * correction rewrites the VIN on the stock item, its vehicle-mdm record and
 * its configuration together, and the dialog says so before it is sent. The
 * new VIN is normalised as the API does (`utils/vin.ts`) and an invalid one is
 * refused in place; the reason is required (the API audits it on every
 * record). A refusal (another record carries the VIN, it is already in stock,
 * a stale version …) is said in the UI language.
 *
 * KAN-535: on a pre-1981 car bought under its chassis number (its `vin` is no
 * VIN) the dialog speaks of the chassis number and takes another one, or the
 * car's real VIN, in the API's one spelling (`normalisedVinOrChassisNumber`).
 *
 * On success the stock item's cache takes the answer, and the configuration
 * and the vehicle-mdm record the correction also rewrote are read again —
 * so every host (the stock detail, the configurator) gets the same refresh.
 */
export function VinCorrectionDialog({ opened, onClose, item, initialVin = '', onCorrected }: VinCorrectionDialogProps) {
  const { t } = useTranslation()
  const hint = useFieldHint()
  const queryClient = useQueryClient()
  const [vin, setVin] = useState(initialVin)
  const [vinError, setVinError] = useState<string | null>(null)
  const [reason, setReason] = useState('')
  const [reasonError, setReasonError] = useState<string | null>(null)
  const [submitError, setSubmitError] = useState<string | null>(null)
  const [submitting, setSubmitting] = useState(false)
  // KAN-266: a retried submit replays the first answer instead of a 409.
  const idempotency = useIdempotencyKey()

  useEffect(() => {
    if (opened) {
      setVin(initialVin)
      setVinError(null)
      setReason('')
      setReasonError(null)
      setSubmitError(null)
      idempotency.renew()
    }
  }, [opened, initialVin, idempotency])

  // KAN-535 (Anto, 2026-11-14, option B): a pre-1981 car bought under its
  // chassis number has that number corrected here — another chassis number,
  // or its real 17-character VIN (which makes it a car with a VIN). A car
  // bought under a VIN keeps a VIN.
  const chassisCar = isChassisNumber(item.vin)
  const normalise = chassisCar ? normalisedVinOrChassisNumber : normalisedVin

  const submit = async () => {
    const newVin = normalise(vin)
    const vinProblem =
      vin.trim() === ''
        ? t(chassisCar ? 'stockDetail.vinCorrection.newChassisNumberRequired' : 'stockDetail.vinCorrection.newVinRequired')
        : newVin === null
          ? t(chassisCar ? 'stockDetail.vinCorrection.invalidChassisNumber' : 'common.invalidVin')
          : newVin === item.vin
            ? t(chassisCar ? 'stockDetail.vinCorrection.chassisNumberUnchanged' : 'apiRefusal.reasons.vin_unchanged')
            : null
    const reasonProblem = reason.trim() === '' ? t('stockDetail.vinCorrection.reasonRequired') : null
    setVinError(vinProblem)
    setReasonError(reasonProblem)
    if (vinProblem || reasonProblem || newVin === null) return

    setSubmitting(true)
    setSubmitError(null)
    try {
      const path = `/inventory/stock-items/${item.id}/vin-correction`
      const body = { newVin, reason: reason.trim() }
      const updated = await api.post<StockItemRead>(path, body, {
        'If-Match': String(item.version),
        ...idempotency.headers([path, body]),
      })
      idempotency.renew()
      queryClient.setQueryData(['stock-item', item.id], updated)
      // The same correction rewrote these two; whoever shows them reads again.
      const configurationId = updated.configurationId ?? item.configurationId
      const vehicleId = updated.vehicleId ?? item.vehicleId
      if (configurationId) void queryClient.invalidateQueries({ queryKey: ['configuration', configurationId] })
      if (vehicleId) void queryClient.invalidateQueries({ queryKey: ['vehicle-mdm', vehicleId] })
      void queryClient.invalidateQueries({ queryKey: ['stock-items'] })
      onCorrected?.(updated)
      onClose()
    } catch (err) {
      setSubmitError(apiRefusalText(t, err, 'stockDetail.vinCorrection.failed'))
    } finally {
      setSubmitting(false)
    }
  }

  return (
    <FormDialog
      opened={opened}
      onClose={onClose}
      title={t('stockDetail.vinCorrection.title')}
      onSubmit={submit}
      submitLabel={t('stockDetail.vinCorrection.submit')}
      cancelLabel={t('stockDetail.vinCorrection.cancel')}
      submitting={submitting}
    >
      <Stack gap="sm">
        <Text size="sm">
          {t(chassisCar ? 'stockDetail.vinCorrection.chassisBody' : 'stockDetail.vinCorrection.body', {
            stockNumber: item.stockNumber,
          })}
        </Text>
        <Group gap="xs">
          <Text size="sm" c="dimmed">
            {t(chassisCar ? 'stockDetail.vinCorrection.currentChassisNumber' : 'stockDetail.vinCorrection.currentVin')}
          </Text>
          {/* UI/UX Specification, Typography: mono is mandatory for identifiers. */}
          <Text size="sm" ff="monospace">
            {item.vin ?? t('common.notSet')}
          </Text>
        </Group>
        <TextInput
          {...(chassisCar
            ? hint(t('stockDetail.vinCorrection.newChassisNumberLabel'), t('stockDetail.vinCorrection.chassisNumberHint'))
            : { label: t('stockDetail.vinCorrection.newVinLabel') })}
          value={vin}
          onChange={(e) => {
            setVin(e.currentTarget.value)
            if (vinError) setVinError(null)
          }}
          onBlur={() => {
            const normalised = normalise(vin)
            if (normalised) setVin(normalised)
          }}
          error={vinError}
          withAsterisk
          ff="monospace"
          data-autofocus
        />
        <Textarea
          label={t('stockDetail.vinCorrection.reasonLabel')}
          value={reason}
          onChange={(e) => {
            setReason(e.currentTarget.value)
            if (reasonError) setReasonError(null)
          }}
          error={reasonError}
          maxLength={REASON_MAX}
          autosize
          minRows={2}
          maxRows={5}
          withAsterisk
        />
        {submitError && (
          <Text size="xs" c="red" role="alert">
            {submitError}
          </Text>
        )}
      </Stack>
    </FormDialog>
  )
}
