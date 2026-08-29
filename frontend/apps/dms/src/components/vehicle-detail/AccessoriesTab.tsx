import { useState } from 'react'
import { ActionIcon, Button, Group, Loader, Stack, Table, TextInput } from '@mantine/core'
import { Plus, X } from 'lucide-react'
import { useTranslation } from 'react-i18next'
import type { VehicleAccessoryRead } from '../../api/types'

interface AccessoriesTabProps {
  accessories: VehicleAccessoryRead[]
  loading: boolean
  onAdd: (accessoryType: string, description: string, validFrom: string) => Promise<void>
  onRemove: (accessoryId: string) => Promise<void>
}

/** FR-V-13: append-only — removing sets validTo, the row is never
 * deleted, and stays visible (struck through) rather than disappearing.
 */
export function AccessoriesTab({ accessories, loading, onAdd, onRemove }: AccessoriesTabProps) {
  const { t } = useTranslation()
  const [type, setType] = useState('')
  const [description, setDescription] = useState('')
  const [submitting, setSubmitting] = useState(false)

  const submit = async () => {
    if (!type) return
    setSubmitting(true)
    try {
      await onAdd(type, description, new Date().toISOString().slice(0, 10))
      setType('')
      setDescription('')
    } finally {
      setSubmitting(false)
    }
  }

  return (
    <Stack gap="md">
      <Group align="flex-end">
        <TextInput label={t('vehicleDetail.accessories.type')} value={type} onChange={(e) => setType(e.currentTarget.value)} />
        <TextInput label={t('vehicleDetail.accessories.description')} value={description} onChange={(e) => setDescription(e.currentTarget.value)} />
        <Button leftSection={<Plus size={14} />} onClick={() => void submit()} loading={submitting} disabled={!type}>
          {t('vehicleDetail.accessories.add')}
        </Button>
      </Group>

      {loading ? (
        <Loader size="sm" />
      ) : (
        <Table>
          <Table.Thead>
            <Table.Tr>
              <Table.Th>{t('vehicleDetail.accessories.type')}</Table.Th>
              <Table.Th>{t('vehicleDetail.accessories.description')}</Table.Th>
              <Table.Th>{t('vehicleDetail.accessories.validFrom')}</Table.Th>
              <Table.Th>{t('vehicleDetail.accessories.validTo')}</Table.Th>
              <Table.Th></Table.Th>
            </Table.Tr>
          </Table.Thead>
          <Table.Tbody>
            {accessories.length === 0 && (
              <Table.Tr>
                <Table.Td colSpan={5} style={{ fontStyle: 'italic', color: 'var(--mantine-color-gray-5)' }}>
                  {t('vehicleDetail.accessories.none')}
                </Table.Td>
              </Table.Tr>
            )}
            {accessories.map((a) => {
              const closed = a.validTo !== null
              return (
                <Table.Tr key={a.id} style={closed ? { opacity: 0.5, textDecoration: 'line-through' } : undefined}>
                  <Table.Td>{a.accessoryType}</Table.Td>
                  <Table.Td>{a.description ?? ''}</Table.Td>
                  <Table.Td>{a.validFrom}</Table.Td>
                  <Table.Td>{a.validTo ?? ''}</Table.Td>
                  <Table.Td>
                    {!closed && (
                      <ActionIcon variant="subtle" color="red" size="sm" onClick={() => void onRemove(a.id)} aria-label={t('vehicleDetail.accessories.remove')}>
                        <X size={14} />
                      </ActionIcon>
                    )}
                  </Table.Td>
                </Table.Tr>
              )
            })}
          </Table.Tbody>
        </Table>
      )}
    </Stack>
  )
}
