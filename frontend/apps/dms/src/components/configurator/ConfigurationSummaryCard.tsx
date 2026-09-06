import { Badge, Card, Group, Stack, Text } from '@mantine/core'
import { AlertTriangle, PencilRuler } from 'lucide-react'
import { useTranslation } from 'react-i18next'
import { SpecGrid } from '@nexotec/ui-kit'
import type { ConfigurationRead } from '../../api/types'

export interface ConfigurationSummaryCardProps {
  configuration: ConfigurationRead
  /** The host decides what "open the configurator" does (re-open the
   * overlay, navigate, …). Omit to hide the affordance. */
  onOpenConfigurator?: () => void
}

/**
 * The **one** read-only rendering of a configuration — the offer
 * workspace, the stock item, the valuation and Vehicle 360 (C-F) all
 * render this exact component, never a copy. Takes a `ConfigurationRead`;
 * fetches nothing.
 */
export function ConfigurationSummaryCard({ configuration, onOpenConfigurator }: ConfigurationSummaryCardProps) {
  const { t } = useTranslation()
  const c = configuration
  const s = c.spec

  const heroParts = [c.brandDisplayName, c.modelGroupName, c.variantName, s.trimName].filter(Boolean)
  const isManual = c.source === 'manual'

  const specItems = [
    { label: t('catalogueBrowse.columns.fuelType'), value: c.fuelType ?? '—' },
    { label: t('catalogueBrowse.columns.drivetrain'), value: c.drivetrain ?? '—' },
    { label: t('catalogueBrowse.columns.transmission'), value: c.transmission ?? '—' },
    { label: t('catalogueBrowse.columns.ps'), value: s.ps ?? '—' },
    { label: t('catalogueBrowse.columns.displacementCcm'), value: s.displacementCcm ?? '—' },
    { label: t('configurator.summary.firstRegistration'), value: c.firstRegistrationDate ?? '—' },
    { label: t('configurator.summary.mileage'), value: c.mileageKm != null ? `${c.mileageKm} km` : '—' },
    { label: t('configurator.summary.colour'), value: c.exteriorColour ?? '—' },
  ]

  return (
    <Card withBorder padding="lg" radius="md" data-testid="configuration-summary-card">
      <Stack gap="md">
        <Group justify="space-between" align="flex-start" wrap="nowrap">
          <Stack gap={4}>
            <Text fw={700} size="lg">
              {heroParts.join(' · ') || t('configurator.summary.untitled')}
            </Text>
            <Group gap="xs">
              {c.catalogueMatchStatus === 'unverified' && (
                <Badge color="yellow" variant="light" leftSection={<AlertTriangle size={12} />}>
                  {t('configurator.badges.unverified')}
                </Badge>
              )}
              {c.catalogueMatchStatus === 'best_match_confirmed' && (
                <Badge color="orange" variant="light">
                  {t('configurator.badges.bestMatchConfirmed')}
                </Badge>
              )}
              {isManual && (
                <Badge color="gray" variant="light" leftSection={<PencilRuler size={12} />}>
                  {t('configurator.badges.manual')}
                </Badge>
              )}
              <Badge variant="light">{t(`configurator.mode.${c.mode}`)}</Badge>
            </Group>
          </Stack>
          {onOpenConfigurator && (
            <Text
              component="button"
              type="button"
              onClick={onOpenConfigurator}
              c="grape"
              fw={600}
              size="sm"
              style={{ background: 'none', border: 'none', cursor: 'pointer' }}
            >
              {t('configurator.summary.open')}
            </Text>
          )}
        </Group>

        <SpecGrid items={specItems} columns={2} />

        {c.options.length > 0 && (
          <Stack gap={4}>
            <Text fw={600} size="sm">
              {t('configurator.summary.options')}
            </Text>
            {c.options.map((o) => (
              <Group key={o.id} justify="space-between">
                <Text size="sm">{o.description}</Text>
                {c.mode === 'build' && o.price != null && !o.isIncluded && (
                  <Text size="sm" c="dimmed">
                    CHF {Number(o.price).toLocaleString()}
                  </Text>
                )}
              </Group>
            ))}
          </Stack>
        )}

        {c.notes && (
          <Text size="sm" c="dimmed">
            {c.notes}
          </Text>
        )}
      </Stack>
    </Card>
  )
}
