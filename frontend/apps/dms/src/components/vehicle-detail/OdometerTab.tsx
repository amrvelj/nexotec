import { useState } from 'react'
import { Alert, Badge, Button, Group, Loader, NumberInput, Select, Stack, Table, TextInput } from '@mantine/core'
import { AlertTriangle, Plus } from 'lucide-react'
import { useTranslation } from 'react-i18next'
import type { OdometerSource, VehicleOdometerReadingRead } from '../../api/types'

interface OdometerTabProps {
  readings: VehicleOdometerReadingRead[]
  loading: boolean
  onAdd: (value: number, readingDate: string, source: OdometerSource) => Promise<void>
}

const SOURCES: OdometerSource[] = ['service_visit', 'sale', 'valuation', 'manual', 'import']

/**
 * Amended FR-V-07: a decreasing reading is accepted AND FLAGGED, never
 * rejected and never hidden — the flag is visible on the reading itself,
 * with no toggle to hide it, matching the response boundary the backend
 * already guarantees (GET .../odometer-readings has no filter parameter
 * that could exclude one).
 */
export function OdometerTab({ readings, loading, onAdd }: OdometerTabProps) {
  const { t } = useTranslation()
  const [value, setValue] = useState<number | ''>('')
  const [date, setDate] = useState('')
  const [source, setSource] = useState<OdometerSource>('manual')
  const [submitting, setSubmitting] = useState(false)

  const submit = async () => {
    if (value === '' || !date) return
    setSubmitting(true)
    try {
      await onAdd(value, date, source)
      setValue('')
      setDate('')
    } finally {
      setSubmitting(false)
    }
  }

  return (
    <Stack gap="md">
      <Group align="flex-end">
        <NumberInput label={t('vehicleDetail.odometer.value')} value={value} onChange={(v) => setValue(typeof v === 'number' ? v : '')} min={0} />
        <TextInput label={t('vehicleDetail.odometer.date')} type="date" value={date} onChange={(e) => setDate(e.currentTarget.value)} />
        <Select
          label={t('vehicleDetail.odometer.source')}
          data={SOURCES.map((s) => ({ value: s, label: t(`vehicleDetail.odometer.sourceLabel.${s}`) }))}
          value={source}
          onChange={(v) => setSource((v as OdometerSource) ?? 'manual')}
        />
        <Button leftSection={<Plus size={14} />} onClick={() => void submit()} loading={submitting} disabled={value === '' || !date}>
          {t('vehicleDetail.odometer.record')}
        </Button>
      </Group>

      {loading ? (
        <Loader size="sm" />
      ) : (
        <Table>
          <Table.Thead>
            <Table.Tr>
              <Table.Th>{t('vehicleDetail.odometer.value')}</Table.Th>
              <Table.Th>{t('vehicleDetail.odometer.date')}</Table.Th>
              <Table.Th>{t('vehicleDetail.odometer.source')}</Table.Th>
              <Table.Th></Table.Th>
            </Table.Tr>
          </Table.Thead>
          <Table.Tbody>
            {readings.length === 0 && (
              <Table.Tr>
                <Table.Td colSpan={4} style={{ fontStyle: 'italic', color: 'var(--mantine-color-gray-5)' }}>
                  {t('vehicleDetail.odometer.none')}
                </Table.Td>
              </Table.Tr>
            )}
            {readings.map((r) => (
              <Table.Tr key={r.id}>
                <Table.Td>{r.value.toLocaleString()}</Table.Td>
                <Table.Td>{r.readingDate}</Table.Td>
                <Table.Td>{t(`vehicleDetail.odometer.sourceLabel.${r.source}`)}</Table.Td>
                <Table.Td>
                  {r.implausible && (
                    <Badge color="yellow" variant="light" leftSection={<AlertTriangle size={12} />}>
                      {t('vehicleDetail.odometer.implausible')}
                    </Badge>
                  )}
                </Table.Td>
              </Table.Tr>
            ))}
          </Table.Tbody>
        </Table>
      )}

      {readings.some((r) => r.implausible) && (
        <Alert color="yellow" icon={<AlertTriangle size={16} />}>
          {t('vehicleDetail.odometer.implausibleNotice')}
        </Alert>
      )}
    </Stack>
  )
}
