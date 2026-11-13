import { useId, useState } from 'react'
import { Alert, Button, Checkbox, Group, Select, Stack, Table, Text } from '@mantine/core'
import { Badge, LanguageBadge, type BadgeTone } from '@nexotec/ui-kit'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Copy } from 'lucide-react'
import { useTranslation } from 'react-i18next'
import { api } from '../../api/client'
import type {
  ConfigurationCopyItem,
  ConfigurationCopyRequest,
  ConfigurationCopyResult,
  ConfigurationCopySourceList,
  ConfigurationCopySubset,
} from '../../api/types'
import { useAuth } from '../../auth/AuthContext'
import { businessRefusalText } from '../../businessOptions'
import { useFieldHint } from '../../hooks/useFieldHint'
import { SectionCard } from './SectionCard'

const SUBSETS: ConfigurationCopySubset[] = ['standard_texts', 'document_template', 'number_ranges']
const OUTCOME_TONE: Record<ConfigurationCopyItem['outcome'], BadgeTone> = {
  created: 'success',
  overwritten: 'warning',
  unchanged: 'slate',
  skipped: 'destructive',
}
const KNOWN_REASONS = new Set([
  'default_replaced',
  'number_range_frozen',
  'number_pattern_location_code_missing',
  'number_range_counter_too_short',
  'standard_text_placeholder_invalid',
  'standard_text_seed_language_missing',
  'whatsapp_approval_reset',
])

/**
 * Business › «Konfiguration übernehmen von …» (KAN-220; D-A-01, FR-A-01):
 * a one-shot copy from a sister Betrieb whose manager flag the user holds
 * too — standard texts, the document template's header notes and the
 * number-range patterns. Always a preview first (the server's dry run, the
 * same writes rolled back), then the confirmation runs the copy. The copies
 * are the Betrieb's own; nothing links them to the source afterwards.
 *
 * Shown to the active Betrieb's manager only — the API answers anyone else
 * 404 — and only when there is a sister Betrieb to copy from.
 */
export function CopyConfigurationSection({ dealershipId }: { dealershipId: string }) {
  const { isActiveDealershipManager } = useAuth()
  const sources = useQuery({
    queryKey: ['dealership', dealershipId, 'copy-configuration-sources'],
    queryFn: () => api.get<ConfigurationCopySourceList>(`/dealerships/${dealershipId}/copy-configuration/sources`),
    enabled: dealershipId !== '' && isActiveDealershipManager,
  })
  if (!isActiveDealershipManager || !sources.data || sources.data.items.length === 0) return null
  return <CopyConfigurationForm dealershipId={dealershipId} sources={sources.data} />
}

function CopyConfigurationForm({ dealershipId, sources }: { dealershipId: string; sources: ConfigurationCopySourceList }) {
  const { t } = useTranslation()
  const queryClient = useQueryClient()
  const [sourceId, setSourceId] = useState<string | null>(null)
  const [subsets, setSubsets] = useState<ConfigurationCopySubset[]>(SUBSETS)
  const [preview, setPreview] = useState<ConfigurationCopyResult | null>(null)
  const [done, setDone] = useState<ConfigurationCopyResult | null>(null)
  const [error, setError] = useState<string | null>(null)

  const run = useMutation({
    mutationFn: (dryRun: boolean) => {
      const body: ConfigurationCopyRequest = { sourceDealershipId: sourceId ?? '', subsets, dryRun }
      return api.post<ConfigurationCopyResult>(`/dealerships/${dealershipId}/copy-configuration`, body)
    },
    onSuccess: (result) => {
      setError(null)
      if (result.dryRun) {
        setPreview(result)
        setDone(null)
      } else {
        setPreview(null)
        setDone(result)
        // The copied texts, template and ranges are read by their own screens.
        void queryClient.invalidateQueries({ queryKey: ['dealership', dealershipId] })
      }
    },
    onError: (err) => setError(businessRefusalText(t, err)),
  })

  const reset = () => {
    setPreview(null)
    setDone(null)
  }
  const toggle = (subset: ConfigurationCopySubset, on: boolean) => {
    reset()
    setSubsets((current) => (on ? SUBSETS.filter((s) => s === subset || current.includes(s)) : current.filter((s) => s !== subset)))
  }
  const changes = preview ? preview.counts.created + preview.counts.overwritten : 0

  return (
    <SectionCard
      title={t('business.copyConfiguration.title')}
      icon={<Copy size={20} />}
      error={error}
      onDismissError={() => setError(null)}
    >
      <Stack gap="md">
        <Text c="dimmed" fz="sm">
          {t('business.copyConfiguration.intro')}
        </Text>
        <Select
          label={t('business.copyConfiguration.source')}
          placeholder={t('business.copyConfiguration.sourcePlaceholder')}
          data={sources.items.map((s) => ({ value: s.id, label: s.legalName }))}
          value={sourceId}
          onChange={(value) => {
            reset()
            setSourceId(value)
          }}
          allowDeselect={false}
        />
        <Checkbox.Group label={t('business.copyConfiguration.subsets')} value={subsets}>
          <Stack gap={6} mt={4}>
            {SUBSETS.map((subset) => (
              <SubsetCheckbox key={subset} subset={subset} onToggle={toggle} />
            ))}
          </Stack>
        </Checkbox.Group>
        <Group justify="flex-end">
          <Button
            variant="default"
            onClick={() => run.mutate(true)}
            disabled={!sourceId || subsets.length === 0}
            loading={run.isPending && run.variables === true}
          >
            {t('business.copyConfiguration.preview')}
          </Button>
        </Group>

        {preview && (
          <Stack gap="sm">
            <Text fw={600}>{t('business.copyConfiguration.previewTitle', { source: preview.sourceLegalName })}</Text>
            <CopyItems result={preview} />
            <Alert color="yellow">
              {t('business.copyConfiguration.confirmNote')}
            </Alert>
            <Group justify="flex-end">
              <Button variant="default" onClick={reset}>
                {t('common.cancel')}
              </Button>
              <Button onClick={() => run.mutate(false)} disabled={changes === 0} loading={run.isPending && run.variables === false}>
                {t('business.copyConfiguration.confirm', { count: changes })}
              </Button>
            </Group>
          </Stack>
        )}

        {done && (
          <Stack gap="sm">
            <Alert color="green">
              {t('business.copyConfiguration.done', {
                source: done.sourceLegalName,
                created: done.counts.created,
                overwritten: done.counts.overwritten,
                skipped: done.counts.skipped,
              })}
            </Alert>
            <CopyItems result={done} />
          </Stack>
        )}
      </Stack>
    </SectionCard>
  )
}

function SubsetCheckbox({
  subset,
  onToggle,
}: {
  subset: ConfigurationCopySubset
  onToggle: (subset: ConfigurationCopySubset, on: boolean) => void
}) {
  const { t } = useTranslation()
  const hint = useFieldHint()
  const descriptionId = useId()
  return (
    <Checkbox
      {...hint(t(`business.copyConfiguration.subset.${subset}`), t(`business.copyConfiguration.subsetHint.${subset}`), {
        descriptionId,
      })}
      value={subset}
      onChange={(e) => onToggle(subset, e.currentTarget.checked)}
    />
  )
}

function CopyItems({ result }: { result: ConfigurationCopyResult }) {
  const { t } = useTranslation()
  if (result.items.length === 0) {
    return (
      <Text c="dimmed" fz="sm">
        {t('business.copyConfiguration.nothing')}
      </Text>
    )
  }
  return (
    <Table striped aria-label={t('business.copyConfiguration.itemsLabel')}>
      <Table.Thead>
        <Table.Tr>
          <Table.Th>{t('business.copyConfiguration.columns.area')}</Table.Th>
          <Table.Th>{t('business.copyConfiguration.columns.item')}</Table.Th>
          <Table.Th>{t('business.copyConfiguration.columns.outcome')}</Table.Th>
        </Table.Tr>
      </Table.Thead>
      <Table.Tbody>
        {result.items.map((item) => (
          <Table.Tr key={`${item.subset}:${item.key}:${item.language ?? ''}`}>
            <Table.Td>{t(`business.copyConfiguration.subset.${item.subset}`)}</Table.Td>
            <Table.Td>
              <ItemLabel item={item} />
            </Table.Td>
            <Table.Td>
              <Stack gap={2} align="flex-start">
                <Badge tone={OUTCOME_TONE[item.outcome]}>
                  {result.dryRun || item.outcome !== 'overwritten'
                    ? t(`business.copyConfiguration.outcome.${item.outcome}`)
                    : t('business.copyConfiguration.outcomeDone.overwritten')}
                </Badge>
                {item.reason && (
                  <Text fz="xs" c="dimmed">
                    {t(`business.copyConfiguration.reason.${KNOWN_REASONS.has(item.reason) ? item.reason : 'other'}`)}
                  </Text>
                )}
              </Stack>
            </Table.Td>
          </Table.Tr>
        ))}
      </Table.Tbody>
    </Table>
  )
}

function ItemLabel({ item }: { item: ConfigurationCopyItem }) {
  const { t } = useTranslation()
  if (item.subset === 'number_ranges') {
    return (
      <Text fz="sm">
        {t(`numberRanges.documentType.${item.key}`)} · <code>{item.label}</code>
      </Text>
    )
  }
  if (item.subset === 'document_template') return <Text fz="sm">{t('business.copyConfiguration.headerNotes')}</Text>
  return (
    <Text fz="sm">
      {item.label} {item.language && <LanguageBadge language={item.language} />}
    </Text>
  )
}
