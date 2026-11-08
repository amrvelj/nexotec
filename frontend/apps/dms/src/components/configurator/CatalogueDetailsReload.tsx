import { useState } from 'react'
import { Button, Group, Stack, Text } from '@mantine/core'
import { FieldLabel } from '@nexotec/ui-kit'
import { useQueryClient } from '@tanstack/react-query'
import { RefreshCw } from 'lucide-react'
import { useTranslation } from 'react-i18next'
import { api } from '../../api/client'
import { toSwissLocale, type SupportedLanguage } from '../../i18n'
import { apiRefusalText } from '../../utils/apiRefusal'
import { formatDateTime } from '../../utils/format'
import type { CatalogueSpecificationRead } from '../../api/types'
import { detailStateLabelKey, type CatalogueDetailName } from './detailState'

const DETAILS: CatalogueDetailName[] = ['options', 'colours', 'tyreSpecs', 'images', 'energyLabel']

export interface CatalogueDetailsReloadProps {
  /** The catalogue variant whose details this Betrieb reloads. */
  catalogueVariantId: string
  /** The host's configuration query, invalidated after a reload so the
   * card's energy label and option texts follow the fresh rows. */
  configurationId: string
}

/**
 * KAN-211 (ADR-123) — «Neu laden», the one refresh of a car's licensed
 * details: one fresh request per detail (options, colours, tyre data,
 * images, Energieetikette) replacing this Betrieb's stored rows, audited.
 * Dealer managers and platform staff only — the host decides who sees it
 * (`DetailsTab.canReloadCatalogueDetails`); the API refuses anyone else.
 * The answer's per-detail states are listed under the button, so a detail
 * the licence refused reads «Nicht in Ihrem Vertrag» right here. A fresh
 * Idempotency-Key per click: a reload is meant to ask the provider again.
 */
export function CatalogueDetailsReload({ catalogueVariantId, configurationId }: CatalogueDetailsReloadProps) {
  const { t, i18n } = useTranslation()
  const queryClient = useQueryClient()
  const locale = toSwissLocale(i18n.language as SupportedLanguage)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [result, setResult] = useState<{ at: string; details: CatalogueSpecificationRead['details'] } | null>(null)

  const reload = async () => {
    setBusy(true)
    setError(null)
    try {
      const spec = await api.post<CatalogueSpecificationRead>(
        `/catalogue/variants/${catalogueVariantId}/details/reload`,
        undefined,
        { 'Idempotency-Key': crypto.randomUUID() },
      )
      setResult({ at: new Date().toISOString(), details: spec.details })
      await Promise.all([
        queryClient.invalidateQueries({ queryKey: ['configuration', configurationId] }),
        queryClient.invalidateQueries({ queryKey: ['catalogue-variant-specification', catalogueVariantId] }),
      ])
    } catch (err) {
      setError(apiRefusalText(t, err, 'stockDetail.configuration.reloadDetailsError'))
    } finally {
      setBusy(false)
    }
  }

  return (
    <Stack gap="xs" data-testid="catalogue-details-reload">
      {/* KAN-443: what the button does is the info icon beside it, never a line of text. */}
      <FieldLabel
        label={
          <Button variant="default" size="xs" leftSection={<RefreshCw size={14} />} onClick={() => void reload()} loading={busy}>
            {t('stockDetail.configuration.reloadDetails')}
          </Button>
        }
        hint={t('stockDetail.configuration.reloadDetailsHint')}
        labels={{ icon: t('common.fieldHintIcon') }}
      />
      {error && (
        <Text size="sm" c="red" role="alert">
          {error}
        </Text>
      )}
      {result && (
        <Stack gap={2} data-testid="catalogue-details-reload-result">
          <Text size="xs" c="dimmed">
            {t('stockDetail.configuration.reloadDetailsDone', { date: formatDateTime(result.at, locale) })}
          </Text>
          {DETAILS.map((detail) => (
            <Group key={detail} gap="xs" wrap="nowrap">
              <Text size="xs" w={120}>
                {t(`configurator.details.names.${detail}`)}
              </Text>
              <Text size="xs" c={result.details[detail].state === 'present' ? 'dimmed' : 'orange'} data-testid={`reload-state-${detail}`}>
                {t(detailStateLabelKey(result.details[detail].state))}
              </Text>
            </Group>
          ))}
        </Stack>
      )}
    </Stack>
  )
}
