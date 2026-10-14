import { useState } from 'react'
import { ActionIcon, Alert, Button, Group, Loader, Stack, Table, TextInput } from '@mantine/core'
import { Plus, X } from 'lucide-react'
import { useTranslation } from 'react-i18next'
import { ApiError } from '../../api/client'
import { toSwissLocale, type SupportedLanguage } from '../../i18n'
import { formatDateOnly } from '../../utils/format'
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
  const { t, i18n } = useTranslation()
  const locale = toSwissLocale(i18n.language as SupportedLanguage)
  const [type, setType] = useState('')
  const [description, setDescription] = useState('')
  const [submitting, setSubmitting] = useState(false)
  const [error, setError] = useState<string | null>(null)

  // KAN-269 (ADR-035): a failed add or remove says so; a failed add keeps
  // what was typed, so the retry — under the same Idempotency-Key — is one
  // click away.
  const submit = async () => {
    if (!type) return
    setSubmitting(true)
    setError(null)
    try {
      await onAdd(type, description, new Date().toISOString().slice(0, 10))
      setType('')
      setDescription('')
    } catch (err) {
      setError(err instanceof ApiError ? err.message : t('vehicleDetail.accessories.saveError'))
    } finally {
      setSubmitting(false)
    }
  }

  const remove = async (accessoryId: string) => {
    setError(null)
    try {
      await onRemove(accessoryId)
    } catch (err) {
      setError(err instanceof ApiError ? err.message : t('vehicleDetail.accessories.removeError'))
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

      {error && (
        <Alert color="red" role="alert">
          {error}
        </Alert>
      )}

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
                  <Table.Td>{formatDateOnly(a.validFrom, locale)}</Table.Td>
                  <Table.Td>{a.validTo ? formatDateOnly(a.validTo, locale) : ''}</Table.Td>
                  <Table.Td>
                    {!closed && (
                      <ActionIcon variant="subtle" color="red" size="sm" onClick={() => void remove(a.id)} aria-label={t('vehicleDetail.accessories.remove')}>
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
