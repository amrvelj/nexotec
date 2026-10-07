import { useState } from 'react'
import { Alert, Badge, Button, Card, Checkbox, Group, Loader, Stack, Table, Text, Title } from '@mantine/core'
import { useQuery } from '@tanstack/react-query'
import { useTranslation } from 'react-i18next'
import { api } from '../../api/client'
import { specFieldLabelKey } from '../../configurationOptions'
import type { ConfigurationRead, ResyncFieldRead } from '../../api/types'

export interface ConfigurationResyncPanelProps {
  configurationId: string
  version: number
  onClose: () => void
  onApplied: (configuration: ConfigurationRead) => void
}

// The five coded fields sit on the carriers beside the spec block; their
// labels are the catalogue grid's own.
const CODED_LABEL_KEYS: Record<string, string> = {
  fuelType: 'catalogueBrowse.columns.fuelType',
  bodyStyle: 'catalogueBrowse.columns.bodyStyle',
  drivetrain: 'catalogueBrowse.columns.drivetrain',
  transmission: 'catalogueBrowse.columns.transmission',
}

function fieldLabelKey(field: string): string {
  const key = field.replace(/_([a-z0-9])/g, (_, c: string) => c.toUpperCase())
  return CODED_LABEL_KEYS[key] ?? specFieldLabelKey(key)
}

/**
 * FR-C-16 — re-sync is **never automatic**. Every field the catalogue now
 * disagrees on is listed, the advisor's own overrides are marked, and
 * **nothing is ticked for them**: only the fields they choose change. The
 * failure this guards against is a configuration on a live offer silently
 * changing its power figure between the quote and the signature.
 *
 * Rendered inline inside the configurator overlay, not as a modal: a
 * Mantine modal sits below the overlay stack and would open behind it.
 */
export function ConfigurationResyncPanel({ configurationId, version, onClose, onApplied }: ConfigurationResyncPanelProps) {
  const { t } = useTranslation()
  const [chosen, setChosen] = useState<Set<string>>(new Set())
  const [submitting, setSubmitting] = useState(false)
  const [error, setError] = useState<string | null>(null)

  const preview = useQuery({
    queryKey: ['configuration-resync', configurationId, version],
    queryFn: () => api.get<ResyncFieldRead[]>(`/configurations/${configurationId}/resync`),
  })

  const toggle = (field: string) =>
    setChosen((prev) => {
      const next = new Set(prev)
      if (next.has(field)) next.delete(field)
      else next.add(field)
      return next
    })

  const apply = async () => {
    setSubmitting(true)
    setError(null)
    try {
      const updated = await api.patch<ConfigurationRead>(
        `/configurations/${configurationId}/resync`,
        { fields: [...chosen] },
        { 'If-Match': String(version) },
      )
      setChosen(new Set())
      onApplied(updated)
    } catch {
      setError(t('configurator.resync.error'))
    } finally {
      setSubmitting(false)
    }
  }

  const fields = preview.data ?? []

  return (
    <Card withBorder padding="md" data-testid="resync-panel">
      <Stack gap="sm">
        <Title order={5}>{t('configurator.resync.title')}</Title>
        {preview.isLoading && <Loader size="sm" />}
        {preview.isError && <Alert color="red">{t('configurator.resync.error')}</Alert>}
        {error && <Alert color="red">{error}</Alert>}
        {preview.data && fields.length === 0 && <Text size="sm">{t('configurator.resync.none')}</Text>}
        {fields.length > 0 && (
          <>
            <Text size="sm" c="dimmed">
              {t('configurator.resync.hint')}
            </Text>
            <Table data-testid="resync-fields">
              <Table.Thead>
                <Table.Tr>
                  <Table.Th />
                  <Table.Th>{t('configurator.resync.field')}</Table.Th>
                  <Table.Th>{t('configurator.resync.current')}</Table.Th>
                  <Table.Th>{t('configurator.resync.catalogue')}</Table.Th>
                </Table.Tr>
              </Table.Thead>
              <Table.Tbody>
                {fields.map((f) => (
                  <Table.Tr key={f.field}>
                    <Table.Td>
                      <Checkbox
                        aria-label={t(fieldLabelKey(f.field))}
                        checked={chosen.has(f.field)}
                        onChange={() => toggle(f.field)}
                      />
                    </Table.Td>
                    <Table.Td>
                      {t(fieldLabelKey(f.field))}{' '}
                      {f.overridden && (
                        <Badge size="xs" variant="light" color="orange">
                          {t('configurator.resync.overridden')}
                        </Badge>
                      )}
                    </Table.Td>
                    <Table.Td>{f.current ?? '—'}</Table.Td>
                    <Table.Td>{f.catalogue ?? '—'}</Table.Td>
                  </Table.Tr>
                ))}
              </Table.Tbody>
            </Table>
          </>
        )}
        <Group justify="flex-end" gap="xs">
          <Button variant="default" size="xs" onClick={onClose}>
            {t('common.cancel')}
          </Button>
          <Button size="xs" onClick={() => void apply()} disabled={chosen.size === 0} loading={submitting}>
            {t('configurator.resync.apply')}
          </Button>
        </Group>
      </Stack>
    </Card>
  )
}
