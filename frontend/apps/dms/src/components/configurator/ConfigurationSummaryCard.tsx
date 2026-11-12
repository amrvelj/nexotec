import { Anchor, Badge, Card, Group, Stack, Text } from '@mantine/core'
import { AlertTriangle, PencilRuler } from 'lucide-react'
import { useTranslation } from 'react-i18next'
import { toSwissLocale, type SupportedLanguage } from '../../i18n'
import { formatCurrencyChf, formatDateOnly, formatNumber } from '../../utils/format'
import { SpecGrid, slate } from '@nexotec/ui-kit'
import type { ConfigurationRead } from '../../api/types'
import {
  CODED_FIELDS,
  CODED_REF_LIST_CODES,
  SPEC_REF_LIST_CODES,
  TECHNICAL_SPEC_GROUPS,
  specFieldLabelKey,
  type SpecFieldDef,
} from '../../configurationOptions'
import { useReferenceLabelLookup } from '../../hooks/useReferenceLabelLookup'
import { providerText } from '../../utils/providerText'
import { axleLabel, rimLabel } from './wheelLabels'

/** KAN-211 — the lists behind `exteriorColourType` / `interiorColourType`. */
const COLOUR_TYPE_LIST_CODES = ['colour_type', 'interior_colour_type']

export interface ConfigurationSummaryCardProps {
  configuration: ConfigurationRead
  /** The host decides what "open the configurator" does (re-open the
   * overlay, navigate, …). Omit to hide the affordance. */
  onOpenConfigurator?: () => void
  /** KAN-510 — the affordance's name where the host says what it does (the
   * stock detail's «Konfiguration bearbeiten»); «Konfigurator öffnen»
   * otherwise. */
  openConfiguratorLabel?: string
  /** KAN-459 — show the read-only technical data block (PRD-Stock FR-I-07
   * block 2) in a spec grid of this many columns (the spec caps it at four).
   * Omit for the compact summary (configurator overlay, valuation dialog). */
  technicalDataColumns?: 1 | 2 | 3 | 4
}

/** A label that already names the unit — "Anhängelast (kg)" — drops it
 * there, because the value now carries it: "1'600 kg". */
function withoutUnitSuffix(label: string, unit: string | undefined): string {
  const suffix = unit ? ` (${unit})` : ''
  return suffix && label.endsWith(suffix) ? label.slice(0, -suffix.length) : label
}

function formatSpecValue(field: SpecFieldDef, value: unknown): string {
  if (field.display === 'year') return String(value)
  if (field.display === 'yearMonth') {
    // `YYYYMM` (ProdVon/ProdBis) → "03.2019"
    const raw = String(value).padStart(6, '0')
    return `${raw.slice(4, 6)}.${raw.slice(0, 4)}`
  }
  if (field.kind === 'int' || field.kind === 'decimal') {
    const n = formatNumber(Number(value), field.fractionDigits)
    return field.unit ? `${n} ${field.unit}` : n
  }
  return String(value)
}

/**
 * The **one** read-only rendering of a configuration — the offer
 * workspace, the stock item, the valuation and Vehicle 360 (C-F) all
 * render this exact component, never a copy. Takes a `ConfigurationRead`
 * and reads the labels of its five coded values (vehicle kind, fuel, body,
 * drive, transmission — KAN-260) from the shared cached lookup
 * (`useReferenceLabelLookup`), in the UI language: every card on a screen
 * shares one request per list, and a code without a label in that language
 * renders `⚠ <code>`, never the raw code silently. With the technical block
 * (KAN-459) it reads the spec block's reference lists the same way; it
 * fetches nothing else.
 */
export function ConfigurationSummaryCard({ configuration, onOpenConfigurator, openConfiguratorLabel, technicalDataColumns }: ConfigurationSummaryCardProps) {
  const { t, i18n } = useTranslation()
  const locale = toSwissLocale(i18n.language as SupportedLanguage)
  const labels = useReferenceLabelLookup(CODED_REF_LIST_CODES)
  const showTechnical = technicalDataColumns != null
  const specLabels = useReferenceLabelLookup(showTechnical ? SPEC_REF_LIST_CODES : [])
  // KAN-211: the hand-named colour types read as labels beside the colour
  // («Deep Black · Metallic»); the lists are asked only when a type is set.
  const colourTypeLabels = useReferenceLabelLookup(
    configuration.exteriorColourType || configuration.interiorColourType ? COLOUR_TYPE_LIST_CODES : [],
  )
  const c = configuration
  const s = c.spec
  // Absent values are "not set" in italic — never zero, never guessed (FR-I-07).
  const notSet = <span style={{ fontStyle: 'italic', color: slate[4] }}>{t('common.notSet')}</span>

  const heroParts = [c.brandDisplayName, c.modelGroupName, c.variantName, s.trimName].filter(Boolean)
  const colourLine = (colour: string | null, type: string | null, listCode: string): string | null => {
    const parts = [colour, type ? colourTypeLabels.label(listCode, type) : null].filter(Boolean)
    return parts.length ? parts.join(' · ') : null
  }
  const exteriorLine = colourLine(c.exteriorColour, c.exteriorColourType, 'colour_type')
  const interiorLine = colourLine(c.interiorColour, c.interiorColourType, 'interior_colour_type')
  const isManual = c.source === 'manual'

  const specItems = [
    ...CODED_FIELDS.map((f) => {
      const code = c[f.key]
      return { label: t(f.labelKey), value: code != null ? labels.label(f.listCode, code) : '—' }
    }),
    // With the technical block these three are in it, once.
    ...(showTechnical
      ? []
      : [
          { label: t('catalogueBrowse.columns.ps'), value: s.ps != null ? formatNumber(s.ps) : '—' },
          { label: t('catalogueBrowse.columns.displacementCcm'), value: s.displacementCcm != null ? formatNumber(s.displacementCcm) : '—' },
        ]),
    { label: t('configurator.summary.firstRegistration'), value: c.firstRegistrationDate ? formatDateOnly(c.firstRegistrationDate, locale) : '—' },
    { label: t('configurator.summary.mileage'), value: c.mileageKm != null ? `${formatNumber(c.mileageKm)} km` : '—' },
    ...(showTechnical ? [] : [{ label: t('configurator.summary.colour'), value: exteriorLine ?? '—' }]),
  ]

  const energyLabelLinks = (c.energyLabels ?? []).filter((label) => label.labelUrl)
  const catalogueWheels = (c.catalogueTyreSpecs ?? []).map((tyre) =>
    [
      tyre.size,
      rimLabel(t, tyre),
      tyre.isStandard ? t('configurator.wheels.standard') : '',
      axleLabel(t, tyre.axle),
      tyre.season ? t(`configurator.wheels.seasonLabel.${tyre.season}`) : '',
    ]
      .filter(Boolean)
      .join(' · '),
  )
  const technicalGroups = showTechnical
    ? [
        ...TECHNICAL_SPEC_GROUPS.map((group) => ({
          title: t(group.titleKey),
          items: [
            ...group.fields.map((f) => {
              const value = s[f.key]
              const absent = value == null || value === ''
              return {
                label: withoutUnitSuffix(t(specFieldLabelKey(f.key as string)), f.unit),
                value: absent ? notSet : f.kind === 'ref' ? specLabels.label(f.listCode!, String(value)) : formatSpecValue(f, value),
              }
            }),
            // KAN-547 / KAN-479 (Anto, option A): the Energieetikette with the
            // year it applied to (ADR-042), read from the variant's per-year
            // ratings — not a block field, so it sits here, not in the registry.
            ...(group.titleKey === 'configurator.spec.groups.consumption'
              ? [
                  {
                    label: t('configurator.spec.fields.energyLabel'),
                    value: c.energyRating
                      ? t('configurator.spec.energyLabelValue', {
                          category: c.energyRating.category.toUpperCase(),
                          year: c.energyRating.year,
                        })
                      : notSet,
                  },
                  // KAN-597 (Anto, 2026-11-11, option A): each gearbox's label
                  // as the provider's PDF, beside the class — for the seller;
                  // no customer document prints it.
                  ...energyLabelLinks.map((label) => ({
                    label: t('configurator.spec.energyLabelPdf'),
                    value: (
                      <Anchor href={label.labelUrl!} target="_blank" rel="noopener noreferrer" size="sm">
                        {label.gearbox
                          ? t('configurator.spec.energyLabelPdfGearbox', {
                              gearbox: label.gearbox,
                              category: label.category.toUpperCase(),
                            })
                          : t('configurator.spec.energyLabelPdf')}
                      </Anchor>
                    ),
                  })),
                ]
              : []),
          ],
        })),
        {
          title: t('configurator.spec.groups.colours'),
          items: [
            { label: t('configurator.colour.exterior'), value: exteriorLine ?? notSet },
            { label: t('configurator.colour.interior'), value: interiorLine ?? notSet },
            // KAN-596 (FR-C-08): the wheels the type approval allows, each
            // dimension beside its rim — what the workshop orders.
            ...(catalogueWheels.length > 0
              ? [
                  {
                    label: t('configurator.wheels.catalogue'),
                    value: (
                      <Stack gap={2}>
                        {catalogueWheels.map((line, i) => (
                          <Text key={i} size="sm">
                            {line}
                          </Text>
                        ))}
                      </Stack>
                    ),
                  },
                ]
              : []),
          ],
        },
      ]
    : []

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
              {openConfiguratorLabel ?? t('configurator.summary.open')}
            </Text>
          )}
        </Group>

        <SpecGrid items={specItems} columns={2} />

        {showTechnical && (
          <Stack gap="md" data-testid="configuration-technical-data">
            <Text fw={700} size="md">
              {t('configurator.summary.technicalData')}
            </Text>
            {technicalGroups.map((group) => (
              <Stack key={group.title} gap={6}>
                <Text fw={600} size="sm" c="dimmed">
                  {group.title}
                </Text>
                <SpecGrid items={group.items} columns={technicalDataColumns} />
              </Stack>
            ))}
          </Stack>
        )}

        {c.options.length > 0 && (
          <Stack gap={4}>
            <Text fw={600} size="sm">
              {t('configurator.summary.options')}
            </Text>
            {c.options.map((o) => (
              <Group key={o.id} justify="space-between">
                <Text size="sm">{providerText(o.descriptions, i18n.language, o.description)}</Text>
                {c.mode === 'build' && o.price != null && !o.isIncluded && (
                  <Text size="sm" c="dimmed">
                    {formatCurrencyChf(Number(o.price))}
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
