import { Badge, Group, Select, Stack, Table, Text, TextInput } from '@mantine/core'
import { ChfInput } from '@nexotec/ui-kit'
import { useTranslation } from 'react-i18next'
import type { CatalogueSpecificationRead } from '../../api/types'
import type { ConfiguratorMode } from '../../configurationOptions'
import { DetailStateNotice } from './DetailStateNotice'

// KAN-75: the axle is the `axle_position` value the provider's AchsenCode
// resolves to through the code map, or null while that code is an unresolved
// mapping gap (or the provider sent none) — then it reads «not classified»,
// never a guessed axle and never the raw code.
const AXLE_I18N_KEY: Record<string, string> = { front: 'front', rear: 'rear', both: 'both' }

function axleLabel(t: (key: string) => string, axle: string | null): string {
  const key = axle == null ? undefined : AXLE_I18N_KEY[axle]
  return t(`configurator.wheels.axlePosition.${key ?? 'unclassified'}`)
}

export interface ColourWheelsFields {
  exteriorColour: string
  interiorColour: string
  /** KAN-211 (Anto, 2026-11-08): the type the advisor names by hand — a
   * `colour_type` value for the paint, an `interior_colour_type` value for
   * the upholstery; `''` is «not set». The identAPI delivers neither. */
  exteriorColourType: string
  interiorColourType: string
  exteriorColourSurcharge: string
  interiorColourSurcharge: string
  wheels: string
  wheelsSurcharge: string
}

export interface ColourTypeOptions {
  /** The list's active values, plus the stored value when the list no
   * longer offers it — labelled and `disabled`, so the advisor sees what is
   * stored and picks a current one (the KAN-96 pattern, built by the overlay's
   * `specRefOptionsOf`). */
  options: { value: string; label: string; disabled?: boolean }[]
  /** The list could not be loaded: the select says so instead of offering
   * nothing silently (the KAN-239 pattern). */
  isError: boolean
}

export interface ColourWheelsTabProps extends ColourWheelsFields {
  /** `undefined` while loading or for a manual (off-catalogue) draft — the
   * free-text fields below work regardless; only the catalogue-assisted
   * picker and the tyre-spec reference table need it. */
  spec: CatalogueSpecificationRead | undefined
  mode: ConfiguratorMode
  /** The active values of the two type lists, labelled in the UI language
   * (the overlay reads them, like `equipmentFeatureOptions` on OptionsTab). */
  exteriorColourTypeOptions: ColourTypeOptions
  interiorColourTypeOptions: ColourTypeOptions
  onChange: (patch: Partial<ColourWheelsFields>) => void
}

/**
 * FR-C-07 (colours) / FR-C-08 (wheels & tyres). The free-text fields are
 * always the primary input — "free text where the provider has no data"
 * applies to every draft, catalogue-matched or not. The catalogue picker
 * and the tyre-spec reference table are an assist, shown only when the
 * catalogue actually has something to offer; picking a catalogue colour
 * fills the text field and its surcharge, but never locks either one —
 * the advisor can still type over both afterwards.
 */
export function ColourWheelsTab({
  spec,
  mode,
  exteriorColour,
  interiorColour,
  exteriorColourType,
  interiorColourType,
  exteriorColourSurcharge,
  interiorColourSurcharge,
  wheels,
  wheelsSurcharge,
  exteriorColourTypeOptions,
  interiorColourTypeOptions,
  onChange,
}: ColourWheelsTabProps) {
  const { t } = useTranslation()
  // A colour whose FarbArt is still an unresolved mapping gap has no type
  // and is offered in neither group until an admin resolves it (KAN-75, A2).
  const exteriorOptions = (spec?.colours ?? []).filter((c) => c.colourType === 'exterior')
  const interiorOptions = (spec?.colours ?? []).filter((c) => c.colourType === 'interior')
  const tyreSpecs = spec?.tyreSpecs ?? []
  // KAN-211 / KAN-212 (ADR-123): why the catalogue offers no colour or no
  // tyre data — a licence gap, a failed request, or a variant the provider no
  // longer lists (a loaded detail with that note) — is said, never implied by
  // an absent picker; the notice renders nothing for a plain loaded detail.
  // The free-text fields work either way.
  const coloursState = spec?.details.colours
  const tyresState = spec?.details.tyreSpecs

  const typeSelect = (
    field: 'exteriorColourType' | 'interiorColourType',
    label: string,
    value: string,
    { options, isError }: ColourTypeOptions,
  ) => {
    // Until the list arrives the stored code stands in for its label (as
    // `refLabelOf` does); the overlay's options carry a retired value, labelled.
    const data = options.some((o) => o.value === value) || value === '' ? options : [...options, { value, label: value, disabled: true }]
    return (
      <Select
        label={label}
        placeholder={t('configurator.colour.pickType')}
        data={data}
        value={value === '' ? null : value}
        onChange={(v) => onChange({ [field]: v ?? '' })}
        error={isError ? t('configurator.colour.typeListUnavailable') : undefined}
        clearable
        // Mantine keeps the clear button out of the accessibility tree (the
        // input clears on Backspace); the name still travels with the button.
        clearButtonProps={{ 'aria-label': t('common.clear') }}
        searchable
        data-testid={`${field}-select`}
      />
    )
  }

  const pickColour = (
    field: 'exteriorColour' | 'interiorColour',
    surchargeField: 'exteriorColourSurcharge' | 'interiorColourSurcharge',
    options: NonNullable<CatalogueSpecificationRead['colours']>,
    colourCode: string | null,
  ) => {
    if (!colourCode) return
    const picked = options.find((c) => c.colourCode === colourCode)
    if (!picked) return
    // The surcharge NumberInput only renders in build mode (FR-C-07: "a
    // price line in build mode") — never write to a field the advisor
    // can't see or clear, and never let a record-mode configuration
    // silently carry a build-only price.
    const patch: Partial<ColourWheelsFields> = { [field]: picked.description }
    if (mode === 'build') patch[surchargeField] = picked.price ?? ''
    onChange(patch)
  }

  return (
    <Stack gap="lg" data-testid="colour-wheels-tab">
      <Stack gap="xs">
        <Text fw={600} size="sm">
          {t('configurator.colour.exterior')}
        </Text>
        {coloursState && <DetailStateNotice detail="colours" state={coloursState} />}
        <Group grow align="flex-end">
          <TextInput
            label={t('configurator.colour.exterior')}
            value={exteriorColour}
            onChange={(e) => onChange({ exteriorColour: e.currentTarget.value })}
          />
          {typeSelect('exteriorColourType', t('configurator.colour.exteriorType'), exteriorColourType, exteriorColourTypeOptions)}
          {exteriorOptions.length > 0 && (
            <Select
              label={t('configurator.colour.pickFromCatalogue')}
              placeholder={t('configurator.colour.pickFromCatalogue')}
              data={exteriorOptions.map((c) => ({ value: c.colourCode, label: c.description }))}
              value={null}
              onChange={(v) => pickColour('exteriorColour', 'exteriorColourSurcharge', exteriorOptions, v)}
              searchable
            />
          )}
          {mode === 'build' && (
            <ChfInput
              label={t('configurator.colour.surcharge')}
              value={exteriorColourSurcharge}
              onChange={(v) => onChange({ exteriorColourSurcharge: v === '' ? '' : String(v) })}
              min={0}
            />
          )}
        </Group>

        <Text fw={600} size="sm" mt="xs">
          {t('configurator.colour.interior')}
        </Text>
        <Group grow align="flex-end">
          <TextInput
            label={t('configurator.colour.interior')}
            value={interiorColour}
            onChange={(e) => onChange({ interiorColour: e.currentTarget.value })}
          />
          {typeSelect('interiorColourType', t('configurator.colour.interiorType'), interiorColourType, interiorColourTypeOptions)}
          {interiorOptions.length > 0 && (
            <Select
              label={t('configurator.colour.pickFromCatalogue')}
              placeholder={t('configurator.colour.pickFromCatalogue')}
              data={interiorOptions.map((c) => ({ value: c.colourCode, label: c.description }))}
              value={null}
              onChange={(v) => pickColour('interiorColour', 'interiorColourSurcharge', interiorOptions, v)}
              searchable
            />
          )}
          {mode === 'build' && (
            <ChfInput
              label={t('configurator.colour.surcharge')}
              value={interiorColourSurcharge}
              onChange={(v) => onChange({ interiorColourSurcharge: v === '' ? '' : String(v) })}
              min={0}
            />
          )}
        </Group>
      </Stack>

      <Stack gap="xs">
        <Text fw={600} size="sm">
          {t('configurator.wheels.title')}
        </Text>
        {tyresState && <DetailStateNotice detail="tyreSpecs" state={tyresState} />}

        {tyreSpecs.length > 0 && (
          <Table data-testid="tyre-spec-table" withTableBorder={false}>
            <Table.Thead>
              <Table.Tr>
                <Table.Th>{t('configurator.wheels.axle')}</Table.Th>
                <Table.Th>{t('configurator.wheels.dimension')}</Table.Th>
                <Table.Th>{t('configurator.wheels.season')}</Table.Th>
                <Table.Th>{t('configurator.wheels.remark')}</Table.Th>
              </Table.Tr>
            </Table.Thead>
            <Table.Tbody>
              {tyreSpecs.map((tyreSpec, i) => (
                <Table.Tr key={i}>
                  <Table.Td>{axleLabel(t, tyreSpec.axle)}</Table.Td>
                  <Table.Td>{tyreSpec.size}</Table.Td>
                  <Table.Td>
                    {tyreSpec.season && (
                      <Badge size="sm" variant="light">
                        {t(`configurator.wheels.seasonLabel.${tyreSpec.season}`)}
                      </Badge>
                    )}
                  </Table.Td>
                  <Table.Td>
                    {tyreSpec.remark && (
                      <Text size="xs" c="dimmed">
                        {tyreSpec.remark}
                      </Text>
                    )}
                  </Table.Td>
                </Table.Tr>
              ))}
            </Table.Tbody>
          </Table>
        )}

        <Group grow align="flex-end">
          <TextInput
            label={t('configurator.wheels.description')}
            value={wheels}
            onChange={(e) => onChange({ wheels: e.currentTarget.value })}
          />
          {mode === 'build' && (
            <ChfInput
              label={t('configurator.colour.surcharge')}
              value={wheelsSurcharge}
              onChange={(v) => onChange({ wheelsSurcharge: v === '' ? '' : String(v) })}
              min={0}
            />
          )}
        </Group>
      </Stack>
    </Stack>
  )
}
