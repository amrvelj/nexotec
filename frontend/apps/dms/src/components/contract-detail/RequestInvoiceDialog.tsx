import { useEffect, useState } from 'react'
import { Alert, Radio, Select, Stack, Text, TextInput } from '@mantine/core'
import { useTranslation } from 'react-i18next'
import { FormDialog } from '@nexotec/ui-kit'
import { ApiError } from '../../api/client'
import { toSwissLocale, type SupportedLanguage } from '../../i18n'
import { apiRefusalText, isStaleVersion } from '../../utils/apiRefusal'
import { formatDateOnly } from '../../utils/format'
import { CANTON_CODES, holdersOf, type PlateHolder } from '../vehicle-detail/plateRefusal'

/** What «Rechnung anfordern» sends (`POST /sales/contracts/{id}/request-invoice`):
 * nothing for «ohne Kontrollschild», else the plate with its canton and the
 * moves the user confirmed. */
export interface RequestInvoiceBody {
  plate?: string
  canton?: string
  moveFrom?: { plateId: string; version: number }[]
}

type Choice = 'with' | 'without'

/**
 * KAN-523 — «Rechnung anfordern» with the plate the car leaves with (Anto,
 * 2026-11-01: the plate is entered in this step, which later becomes
 * «Rechnung generieren»). The user says which plate, or «ohne
 * Kontrollschild». When the plate is current on another car of the group the
 * API refuses and names that car; the dialog asks «move it to this car?»,
 * exactly as Vehicle 360's «Kontrollschild erfassen» does, and sends the
 * request again with the answer. No Wechselschild here: a sold car leaves
 * with its own plate.
 */
export function RequestInvoiceDialog({
  opened,
  onClose,
  onRequest,
}: {
  opened: boolean
  onClose: () => void
  /** Sends the request; rejects with the API's refusal. */
  onRequest: (body: RequestInvoiceBody | undefined) => Promise<void>
}) {
  const { t, i18n } = useTranslation()
  const locale = toSwissLocale(i18n.language as SupportedLanguage)
  const [choice, setChoice] = useState<Choice | null>(null)
  const [canton, setCanton] = useState<string | null>(null)
  const [plate, setPlate] = useState('')
  const [holders, setHolders] = useState<PlateHolder[] | null>(null)
  const [submitting, setSubmitting] = useState(false)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    if (!opened) return
    setChoice(null)
    setCanton(null)
    setPlate('')
    setHolders(null)
    setError(null)
  }, [opened])

  const withPlate = choice === 'with'
  const complete = choice === 'without' || (withPlate && !!canton && plate.trim() !== '')
  const confirming = holders !== null && holders.length > 0

  const send = async () => {
    if (!complete) return
    setSubmitting(true)
    setError(null)
    const body: RequestInvoiceBody | undefined = withPlate
      ? {
          plate: plate.trim(),
          canton: canton!,
          ...(confirming ? { moveFrom: holders.map((h) => ({ plateId: h.plateId, version: h.version })) } : {}),
        }
      : undefined
    try {
      await onRequest(body)
      onClose()
    } catch (err) {
      const found = holdersOf(err)
      if (found) setHolders(found)
      else if (isStaleVersion(err)) {
        // The other car's assignment, or the contract, changed meanwhile: ask
        // the register again on the next click rather than resend old versions.
        setHolders(null)
        setError(apiRefusalText(t, err, 'contractDetail.requestInvoice.error'))
      } else if (err instanceof ApiError && err.details?.holdersWithheld === true) {
        // Past the plate-read limit the refusal names no car (ADR-085).
        setError(`${apiRefusalText(t, err, 'contractDetail.requestInvoice.error')} ${t('vehicleDetail.plates.limitReached')}`)
      } else setError(apiRefusalText(t, err, 'contractDetail.requestInvoice.error'))
    } finally {
      setSubmitting(false)
    }
  }

  const typed = `${canton ?? ''} ${plate.trim()}`.trim()

  return (
    <FormDialog
      opened={opened}
      onClose={onClose}
      title={t('contractDetail.requestInvoice.title')}
      submitLabel={confirming ? t('contractDetail.requestInvoice.moveSubmit') : t('contractDetail.requestInvoice.submit')}
      cancelLabel={t('common.cancel')}
      submitting={submitting}
      submitDisabled={!complete}
      onSubmit={send}
    >
      <Stack gap="sm">
        {error && (
          <Alert color="red" role="alert">
            {error}
          </Alert>
        )}
        <Text size="sm">{t('contractDetail.requestInvoice.hint')}</Text>
        <Radio.Group
          value={choice}
          onChange={(value) => {
            setChoice(value as Choice)
            setHolders(null)
          }}
        >
          <Stack gap="xs">
            <Radio value="with" label={t('contractDetail.requestInvoice.withPlate')} disabled={confirming} />
            <Radio value="without" label={t('contractDetail.requestInvoice.withoutPlate')} disabled={confirming} />
          </Stack>
        </Radio.Group>
        {withPlate && (
          <>
            <Select
              label={t('vehicleDetail.plates.canton')}
              data={CANTON_CODES}
              value={canton}
              onChange={(value) => {
                setCanton(value)
                setHolders(null)
              }}
              searchable
              required
              disabled={confirming}
            />
            <TextInput
              label={t('vehicleDetail.plates.number')}
              placeholder="123456"
              value={plate}
              onChange={(e) => {
                setPlate(e.currentTarget.value)
                setHolders(null)
              }}
              required
              disabled={confirming}
            />
          </>
        )}
        {confirming && (
          <Alert color="yellow" role="status" title={t('vehicleDetail.plates.onOtherVehicleTitle')}>
            <Stack gap="xs">
              {holders.map((holder) => (
                <Text size="sm" key={holder.plateId}>
                  {t('vehicleDetail.plates.onOtherVehicle', {
                    plate: typed,
                    vehicle: holder.vehicleNumber ?? '',
                    vin: holder.vin ?? '',
                    since: formatDateOnly(holder.validFrom, locale),
                  })}
                </Text>
              ))}
              <Text size="sm">{t('vehicleDetail.plates.moveQuestion')}</Text>
            </Stack>
          </Alert>
        )}
      </Stack>
    </FormDialog>
  )
}
