import { Alert, Badge, Loader, Table } from '@mantine/core'
import { useTranslation } from 'react-i18next'
import type { VehiclePlateRead } from '../../api/types'

interface PlatesTabProps {
  plates: VehiclePlateRead[]
  loading: boolean
  /** KAN-231: the user has read the plates of too many vehicles in the
   * window; the API refused (403, `plate_read_limit_reached`). */
  limitReached?: boolean
}

/** FR-V-16 Plates tab — this vehicle's own Kontrollschild history. A
 * Wechselschild pair (shared plateGroupId) is shown as a normal pair of
 * rows, not flagged — the overlap is expected (ADR-039).
 */
export function PlatesTab({ plates, loading, limitReached = false }: PlatesTabProps) {
  const { t } = useTranslation()
  if (loading) return <Loader size="sm" />
  if (limitReached) return <Alert color="yellow">{t('vehicleDetail.plates.limitReached')}</Alert>

  return (
    <Table>
      <Table.Thead>
        <Table.Tr>
          <Table.Th>{t('vehicleDetail.plates.plate')}</Table.Th>
          <Table.Th>{t('vehicleDetail.plates.canton')}</Table.Th>
          <Table.Th>{t('vehicleDetail.plates.validFrom')}</Table.Th>
          <Table.Th>{t('vehicleDetail.plates.validTo')}</Table.Th>
          <Table.Th></Table.Th>
        </Table.Tr>
      </Table.Thead>
      <Table.Tbody>
        {plates.length === 0 && (
          <Table.Tr>
            <Table.Td colSpan={5} style={{ fontStyle: 'italic', color: 'var(--mantine-color-gray-5)' }}>
              {t('vehicleDetail.plates.none')}
            </Table.Td>
          </Table.Tr>
        )}
        {plates.map((p) => (
          <Table.Tr key={p.id}>
            <Table.Td style={{ fontFamily: 'monospace' }}>{p.plate}</Table.Td>
            <Table.Td>{p.canton}</Table.Td>
            <Table.Td>{p.validFrom}</Table.Td>
            <Table.Td>{p.validTo ?? <em>{t('vehicleDetail.plates.current')}</em>}</Table.Td>
            <Table.Td>
              {p.isInterchangeable && <Badge variant="light" color="grape">{t('vehicleDetail.plates.wechselschild')}</Badge>}
            </Table.Td>
          </Table.Tr>
        ))}
      </Table.Tbody>
    </Table>
  )
}
