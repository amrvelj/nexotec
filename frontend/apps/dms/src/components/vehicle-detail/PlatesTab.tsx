import { Alert, Badge, Button, Group, Loader, Stack, Table } from '@mantine/core'
import { Plus } from 'lucide-react'
import { useTranslation } from 'react-i18next'
import { toSwissLocale, type SupportedLanguage } from '../../i18n'
import { formatDateOnly, todayLocalIsoDate } from '../../utils/format'
import type { VehiclePlateRead } from '../../api/types'

interface PlatesTabProps {
  plates: VehiclePlateRead[]
  loading: boolean
  /** KAN-231: the user has read the plates of too many vehicles in the
   * window; the API refused (403, `plate_read_limit_reached`). */
  limitReached?: boolean
  /** KAN-467 — opens «Kontrollschild erfassen». */
  onRecord: () => void
  /** KAN-467 — «beenden» on a current assignment. */
  onEnd: (plate: VehiclePlateRead) => void
}

/** FR-V-16 Plates tab — this vehicle's own Kontrollschild history in the
 * group's register (KAN-467). A Wechselschild pair (shared plateGroupId) is
 * shown as a normal pair of rows, not flagged — the overlap is expected
 * (ADR-039). «Kontrollschild erfassen» records one; «beenden» ends a current
 * one (the row stays).
 */
export function PlatesTab({ plates, loading, limitReached = false, onRecord, onEnd }: PlatesTabProps) {
  const { t, i18n } = useTranslation()
  const locale = toSwissLocale(i18n.language as SupportedLanguage)
  if (loading) return <Loader size="sm" />
  if (limitReached) return <Alert color="yellow">{t('vehicleDetail.plates.limitReached')}</Alert>
  const today = todayLocalIsoDate()

  return (
    <Stack gap="md">
    <Group>
      <Button leftSection={<Plus size={14} />} onClick={onRecord}>
        {t('vehicleDetail.header.recordPlate')}
      </Button>
    </Group>
    <Table>
      <Table.Thead>
        <Table.Tr>
          <Table.Th>{t('vehicleDetail.plates.plate')}</Table.Th>
          <Table.Th>{t('vehicleDetail.plates.canton')}</Table.Th>
          <Table.Th>{t('vehicleDetail.plates.validFrom')}</Table.Th>
          <Table.Th>{t('vehicleDetail.plates.validTo')}</Table.Th>
          <Table.Th></Table.Th>
          <Table.Th></Table.Th>
        </Table.Tr>
      </Table.Thead>
      <Table.Tbody>
        {plates.length === 0 && (
          <Table.Tr>
            <Table.Td colSpan={6} style={{ fontStyle: 'italic', color: 'var(--mantine-color-gray-5)' }}>
              {t('vehicleDetail.plates.none')}
            </Table.Td>
          </Table.Tr>
        )}
        {plates.map((p) => (
          <Table.Tr key={p.id}>
            <Table.Td style={{ fontFamily: 'monospace' }}>{p.plate}</Table.Td>
            <Table.Td>{p.canton}</Table.Td>
            <Table.Td>{formatDateOnly(p.validFrom, locale)}</Table.Td>
            <Table.Td>{p.validTo ? formatDateOnly(p.validTo, locale) : <em>{t('vehicleDetail.plates.current')}</em>}</Table.Td>
            <Table.Td>
              {p.isInterchangeable && <Badge variant="light" color="grape">{t('vehicleDetail.plates.wechselschild')}</Badge>}
            </Table.Td>
            <Table.Td>
              {(p.validTo === null || p.validTo >= today) && (
                <Button variant="subtle" size="xs" onClick={() => onEnd(p)}>
                  {t('vehicleDetail.plates.end')}
                </Button>
              )}
            </Table.Td>
          </Table.Tr>
        ))}
      </Table.Tbody>
    </Table>
    </Stack>
  )
}
