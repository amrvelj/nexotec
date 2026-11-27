import { useEffect, useState } from 'react'
import { NumberInput, SimpleGrid, Text, TextInput } from '@mantine/core'
import { useTranslation } from 'react-i18next'
import { FormDialog } from '@nexotec/ui-kit'
import { api } from '../../../../api/client'
import type { ArrivalCreate, ServiceOrderRead } from '../../../../api/types'
import { useIdempotencyKey } from '../../../../hooks/useIdempotencyKey'
import { formatNumber } from '../../../../utils/format'
import { RefusalAlert } from './RefusalAlert'

/**
 * D14 «Fahrzeug ist da» (KAN-813; the slice page, table 3.4): the mileage
 * (required, a whole number from 0), the key tag, the parking place and a
 * remark, with the car's last reading beside it. A lower mileage than the
 * last is taken — Vehicles flags it (check 22). The order carries an
 * identified car (ruling 36), so its VIN is never asked here.
 */
export function ArrivalDialog({
  opened,
  order,
  onClose,
  onSaved,
  onReload,
}: {
  opened: boolean
  order: ServiceOrderRead
  onClose: () => void
  onSaved: (order: ServiceOrderRead) => void
  onReload: () => void
}) {
  const { t } = useTranslation()
  const idempotency = useIdempotencyKey()
  const [mileage, setMileage] = useState<number | ''>('')
  const [keyTag, setKeyTag] = useState('')
  const [parkingPlace, setParkingPlace] = useState('')
  const [remark, setRemark] = useState('')
  const [missing, setMissing] = useState(false)
  const [submitting, setSubmitting] = useState(false)
  const [error, setError] = useState<unknown>(null)

  useEffect(() => {
    if (opened) {
      setMileage('')
      setKeyTag('')
      setParkingPlace('')
      setRemark('')
      setMissing(false)
      setError(null)
    }
  }, [opened])

  const last = order.vehicleMileage ?? null
  const below = typeof mileage === 'number' && last !== null && mileage < last

  const submit = async () => {
    // A mirror of the server's «a mileage is required»: said at the field, never a held-back button.
    if (mileage === '') {
      setMissing(true)
      return
    }
    setSubmitting(true)
    setError(null)
    try {
      const body: ArrivalCreate = { mileage }
      if (keyTag.trim()) body.keyTag = keyTag.trim()
      if (parkingPlace.trim()) body.parkingPlace = parkingPlace.trim()
      if (remark.trim()) body.remark = remark.trim()
      const path = `/service-orders/${order.id}/arrival`
      const updated = await api.post<ServiceOrderRead>(path, body, {
        'If-Match': String(order.version),
        ...idempotency.headers([path, body]),
      })
      idempotency.renew()
      onSaved(updated)
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
      title={t('serviceOrder.arrival.title')}
      onSubmit={submit}
      submitLabel={t('serviceOrder.arrival.submit')}
      cancelLabel={t('common.cancel')}
      submitting={submitting}
      size="lg"
    >
      <SimpleGrid cols={{ base: 1, sm: 2 }}>
        <NumberInput
          label={t('serviceOrder.arrival.mileage')}
          value={mileage}
          onChange={(value) => {
            setMileage(typeof value === 'number' ? value : value === '' ? '' : Number(value))
            setMissing(false)
          }}
          min={0}
          max={9_999_999}
          clampBehavior="strict"
          allowNegative={false}
          allowDecimal={false}
          thousandSeparator="'"
          suffix=" km"
          required
          data-autofocus
          error={missing ? t('serviceOrder.arrival.mileageMissing') : undefined}
        />
        <TextInput
          label={t('serviceOrder.arrival.keyTag')}
          value={keyTag}
          onChange={(e) => setKeyTag(e.currentTarget.value)}
          maxLength={50}
        />
        <TextInput
          label={t('serviceOrder.arrival.parkingPlace')}
          value={parkingPlace}
          onChange={(e) => setParkingPlace(e.currentTarget.value)}
          maxLength={100}
        />
      </SimpleGrid>
      <TextInput
        mt="sm"
        label={t('serviceOrder.arrival.remark')}
        value={remark}
        onChange={(e) => setRemark(e.currentTarget.value)}
        maxLength={1000}
      />
      <Text size="sm" c="dimmed" mt="sm" data-testid="arrival-last-mileage">
        {last === null ? t('serviceOrder.arrival.noLastMileage') : t('serviceOrder.arrival.lastMileage', { km: formatNumber(last) })}
      </Text>
      {below && (
        <Text size="sm" c="orange" data-testid="arrival-below-last">
          {t('serviceOrder.arrival.belowLast')}
        </Text>
      )}
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
