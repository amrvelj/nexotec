import { useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { Button, Container, Group, Select, Stack, TextInput, Title } from '@mantine/core'
import { useTranslation } from 'react-i18next'
import { semantic, useSetBreadcrumb } from '@nexotec/ui-kit'
import { api } from '../api/client'
import { translatedStockConditionOptions } from '../stockOptions'
import type { StockItemCondition, StockItemRead } from '../api/types'

/**
 * FR-I-01 "Fahrzeug aufnehmen" — the minimal manual-entry path (vehicle
 * label + condition, optionally a known VIN for an already-in-stock car).
 * The pipeline auto-create paths from Sales (manual configuration,
 * trade-in) land in PR-2 as an event consumer — this page is the
 * hand-typed door, not the only one.
 */
export function StockCreatePage() {
  const { t } = useTranslation()
  useSetBreadcrumb([t('shell.nav.inventory'), t('stockCreate.title')])
  const navigate = useNavigate()

  const [vehicleLabel, setVehicleLabel] = useState('')
  const [condition, setCondition] = useState<StockItemCondition>('used')
  const [vin, setVin] = useState('')
  const [submitting, setSubmitting] = useState(false)
  const [error, setError] = useState<string | null>(null)

  const conditionOptions = translatedStockConditionOptions(t)

  const submit = async () => {
    setSubmitting(true)
    setError(null)
    try {
      const created = await api.post<StockItemRead>('/inventory/stock-items', {
        vehicleLabel,
        condition,
        vin: vin || undefined,
      })
      navigate(`/stock/${created.id}`)
    } catch {
      setError(t('stockCreate.errors.somethingWentWrong'))
    } finally {
      setSubmitting(false)
    }
  }

  return (
    <Container py="xl" size="sm">
      <Stack gap="xl">
        <Title order={2}>{t('stockCreate.title')}</Title>
        <Stack gap="md">
          <TextInput
            label={t('stockCreate.fields.vehicleLabel')}
            value={vehicleLabel}
            onChange={(e) => setVehicleLabel(e.currentTarget.value)}
            required
          />
          <Select
            label={t('stockCreate.fields.condition')}
            data={conditionOptions.map((o) => ({ value: o.value, label: o.label }))}
            value={condition}
            onChange={(v) => setCondition((v as StockItemCondition) ?? 'used')}
            required
          />
          <TextInput
            label={t('stockCreate.fields.vin')}
            description={t('stockCreate.fields.vinDescription')}
            value={vin}
            onChange={(e) => setVin(e.currentTarget.value.toUpperCase())}
          />
          {error && <span style={{ color: semantic.destructive.text }}>{error}</span>}
          <Group justify="flex-end">
            <Button variant="default" onClick={() => navigate('/stock')}>
              {t('common.cancel')}
            </Button>
            <Button onClick={submit} loading={submitting} disabled={!vehicleLabel}>
              {t('stockCreate.submit')}
            </Button>
          </Group>
        </Stack>
      </Stack>
    </Container>
  )
}
