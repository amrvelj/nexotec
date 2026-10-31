import { useState } from 'react'
import { Alert, Badge, Button, Card, Checkbox, Group, Loader, Stack, Table, Text, Title } from '@mantine/core'
import { useQuery } from '@tanstack/react-query'
import { useTranslation } from 'react-i18next'
import { api } from '../../api/client'
import { ALL_SPEC_FIELDS, CODED_FIELDS, specFieldLabelKey } from '../../configurationOptions'
import { useReferenceLabelLookup, type ReferenceLabelLookup } from '../../hooks/useReferenceLabelLookup'
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

// KAN-190: a coded field and a spec field on a reference list hold a code;
// it is shown by its label in the UI language, as the summary card shows it.
const LIST_CODES: Record<string, string> = Object.fromEntries([
  ...CODED_FIELDS.map((f) => [f.key, f.listCode] as const),
  ...ALL_SPEC_FIELDS.filter((f) => f.kind === 'ref').map((f) => [f.key as string, f.listCode!] as const),
])

const camelKey = (field: string) => field.replace(/_([a-z0-9])/g, (_, c: string) => c.toUpperCase())

function display(field: string, value: ResyncFieldRead['current'], labels: ReferenceLabelLookup): string {
  if (value === null || value === undefined) return '—'
  if (typeof value === 'boolean') return value ? '✓' : '✗'
  const listCode = LIST_CODES[camelKey(field)]
  return listCode ? labels.label(listCode, String(value)) : String(value)
}

function fieldLabelKey(field: string): string {
  const key = camelKey(field)
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
  const listCodes = [...new Set(fields.map((f) => LIST_CODES[camelKey(f.field)]).filter((code): code is string => code != null))]
  const labels = useReferenceLabelLookup(listCodes)

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
                    <Table.Td>{display(f.field, f.current, labels)}</Table.Td>
                    <Table.Td>{display(f.field, f.catalogue, labels)}</Table.Td>
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
