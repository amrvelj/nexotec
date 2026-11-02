import { useEffect, useState } from 'react'
import { Alert, Button, Select, Stack, Text, TextInput } from '@mantine/core'
import { useTranslation } from 'react-i18next'
import { FormDialog } from '@nexotec/ui-kit'
import { ApiError } from '../../api/client'
import type { VehiclePlateRead } from '../../api/types'
import { CANTON_OPTIONS } from '../../customerOptions'
import { toSwissLocale, type SupportedLanguage } from '../../i18n'
import { apiRefusalText, isStaleVersion } from '../../utils/apiRefusal'
import { formatDateOnly, todayLocalIsoDate } from '../../utils/format'

/** What «Kontrollschild erfassen» sends (`POST /vehicle-mdm/{id}/plates`). */
export interface RecordPlateRequest {
  plate: string
  canton: string
  validFrom: string
  validTo: string | null
  /** The other cars' assignments the user answered about, at the version
   * the refusal showed — the If-Match of the rows the answer changes. */
  moveFrom?: PlateVersionRef[]
  wechselschildWith?: PlateVersionRef
}

interface PlateVersionRef {
  plateId: string
  version: number
}

/** One car of the group the plate is current on, from the 409's details. */
export interface PlateHolder {
  plateId: string
  vehicleId: string
  vehicleNumber: string | null
  vin: string | null
  validFrom: string
  version: number
}

export const CANTON_CODES = CANTON_OPTIONS.map((option) => option.value)

/** The cars a 409 `plate_current_on_other_vehicle` names — also the refusal
 * of «Rechnung anfordern» with a plate (KAN-523). */
export function holdersOf(err: unknown): PlateHolder[] | null {
  if (!(err instanceof ApiError) || err.status !== 409) return null
  if (err.details?.reason !== 'plate_current_on_other_vehicle') return null
  return (err.details.holders as PlateHolder[] | undefined) ?? null
}

/**
 * KAN-467 — «Kontrollschild erfassen». A plate is canton + number (ZH 123456
 * ≠ BE 123456). When the plate is current on another car of the group the
 * API refuses and names that car (Anto, 2026-10-31: ask the user); the dialog
 * then asks «move it to this car?» — or, for one other car, offers the
 * Wechselschild — and sends the same recording again with the answer.
 */
export function RecordPlateDialog({
  opened,
  onClose,
  onRecord,
}: {
  opened: boolean
  onClose: () => void
  onRecord: (request: RecordPlateRequest) => Promise<void>
}) {
  const { t, i18n } = useTranslation()
  const locale = toSwissLocale(i18n.language as SupportedLanguage)
  const [canton, setCanton] = useState<string | null>(null)
  const [plate, setPlate] = useState('')
  const [validFrom, setValidFrom] = useState(todayLocalIsoDate())
  const [validTo, setValidTo] = useState('')
  const [holders, setHolders] = useState<PlateHolder[] | null>(null)
  const [submitting, setSubmitting] = useState(false)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    if (!opened) return
    setCanton(null)
    setPlate('')
    setValidFrom(todayLocalIsoDate())
    setValidTo('')
    setHolders(null)
    setError(null)
  }, [opened])

  const send = async (answer: Pick<RecordPlateRequest, 'moveFrom' | 'wechselschildWith'> = {}) => {
    if (!canton || !plate.trim() || !validFrom) return
    setSubmitting(true)
    setError(null)
    try {
      await onRecord({ plate: plate.trim(), canton, validFrom, validTo: validTo || null, ...answer })
      onClose()
    } catch (err) {
      const found = holdersOf(err)
      if (found) setHolders(found)
      else if (isStaleVersion(err)) {
        // The other car's assignment changed since the refusal showed it: the
        // shown cars are out of date. Drop them, so the next «Erfassen» asks
        // the register again instead of resending the stale versions.
        setHolders(null)
        setError(apiRefusalText(t, err, 'vehicleDetail.plates.saveError'))
      }
      else if (err instanceof ApiError && err.details?.holdersWithheld === true) {
        // Past the plate-read limit the refusal names no car (ADR-085).
        setError(`${apiRefusalText(t, err, 'vehicleDetail.plates.saveError')} ${t('vehicleDetail.plates.limitReached')}`)
      } else setError(apiRefusalText(t, err, 'vehicleDetail.plates.saveError'))
    } finally {
      setSubmitting(false)
    }
  }

  const confirming = holders !== null && holders.length > 0
  const typed = `${canton ?? ''} ${plate.trim()}`.trim()

  return (
    <FormDialog
      opened={opened}
      onClose={onClose}
      title={t('vehicleDetail.plates.recordTitle')}
      submitLabel={confirming ? t('vehicleDetail.plates.moveConfirm') : t('vehicleDetail.plates.recordSubmit')}
      cancelLabel={t('common.cancel')}
      submitting={submitting}
      submitDisabled={!canton || !plate.trim() || !validFrom}
      onSubmit={() =>
        confirming ? send({ moveFrom: holders.map((h) => ({ plateId: h.plateId, version: h.version })) }) : send()
      }
    >
      <Stack gap="sm">
        {error && (
          <Alert color="red" role="alert">
            {error}
          </Alert>
        )}
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
        <TextInput
          label={t('vehicleDetail.plates.validFrom')}
          type="date"
          value={validFrom}
          onChange={(e) => setValidFrom(e.currentTarget.value)}
          required
          disabled={confirming}
        />
        <TextInput
          label={t('vehicleDetail.plates.validTo')}
          type="date"
          value={validTo}
          onChange={(e) => setValidTo(e.currentTarget.value)}
          disabled={confirming}
        />
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
              {holders.length === 1 && (
                <Button
                  variant="subtle"
                  size="xs"
                  style={{ alignSelf: 'flex-start' }}
                  disabled={submitting}
                  onClick={() => void send({ wechselschildWith: { plateId: holders[0]!.plateId, version: holders[0]!.version } })}
                >
                  {t('vehicleDetail.plates.formWechselschild')}
                </Button>
              )}
            </Stack>
          </Alert>
        )}
      </Stack>
    </FormDialog>
  )
}

/** KAN-467 — «beenden»: the last day the plate was on the car; the row stays. */
export function EndPlateDialog({
  plate,
  onClose,
  onEnd,
}: {
  plate: VehiclePlateRead | null
  onClose: () => void
  onEnd: (plate: VehiclePlateRead, validTo: string) => Promise<void>
}) {
  const { t } = useTranslation()
  const [validTo, setValidTo] = useState(todayLocalIsoDate())
  const [submitting, setSubmitting] = useState(false)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    if (!plate) return
    setValidTo(todayLocalIsoDate())
    setError(null)
  }, [plate])

  return (
    <FormDialog
      opened={plate !== null}
      onClose={onClose}
      title={t('vehicleDetail.plates.endTitle', { plate: plate?.plate ?? '' })}
      submitLabel={t('vehicleDetail.plates.end')}
      cancelLabel={t('common.cancel')}
      submitting={submitting}
      submitDisabled={!validTo}
      onSubmit={async () => {
        if (!plate || !validTo) return
        setSubmitting(true)
        setError(null)
        try {
          await onEnd(plate, validTo)
          onClose()
        } catch (err) {
          setError(apiRefusalText(t, err, 'vehicleDetail.plates.saveError'))
        } finally {
          setSubmitting(false)
        }
      }}
    >
      {error && (
        <Alert color="red" role="alert">
          {error}
        </Alert>
      )}
      <TextInput
        label={t('vehicleDetail.plates.endsOn')}
        type="date"
        value={validTo}
        onChange={(e) => setValidTo(e.currentTarget.value)}
        required
      />
    </FormDialog>
  )
}
