import { Badge, Group, NumberInput, Select, Stack, Table, Text, TextInput } from '@mantine/core'
import { useTranslation } from 'react-i18next'
import type { CatalogueSpecificationRead } from '../../api/types'
import type { ConfiguratorMode } from '../../configurationOptions'

// Axle codes 4-7 are undocumented, per-manufacturer variants the backend
// deliberately keeps distinct as `variant_N` rather than bucketing them
// (see app/integration/adapters/auto_i_dat_soap.py::_achsen_code_to_axle) —
// there is no natural-language label for those, so they render as their
// raw code rather than tripping the loud-missing-key i18n check for a key
// that was never meant to exist.
const AXLE_I18N_KEY: Record<string, string> = { front: 'front', rear: 'rear', both: 'both' }

function axleLabel(t: (key: string) => string, axle: string): string {
  const key = AXLE_I18N_KEY[axle]
  return key ? t(`configurator.wheels.axlePosition.${key}`) : axle
}

export interface ColourWheelsFields {
  exteriorColour: string
  interiorColour: string
  exteriorColourSurcharge: string
  interiorColourSurcharge: string
  wheels: string
  wheelsSurcharge: string
}

export interface ColourWheelsTabProps extends ColourWheelsFields {
  /** `undefined` while loading or for a manual (off-catalogue) draft — the
   * free-text fields below work regardless; only the catalogue-assisted
   * picker and the tyre-spec reference table need it. */
  spec: CatalogueSpecificationRead | undefined
  mode: ConfiguratorMode
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
  exteriorColourSurcharge,
  interiorColourSurcharge,
  wheels,
  wheelsSurcharge,
  onChange,
}: ColourWheelsTabProps) {
  const { t } = useTranslation()
  const exteriorOptions = (spec?.colours ?? []).filter((c) => c.colourType === 'exterior')
  const interiorOptions = (spec?.colours ?? []).filter((c) => c.colourType === 'interior')
  const tyreSpecs = spec?.tyreSpecs ?? []

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
        <Group grow align="flex-end">
          <TextInput
            label={t('configurator.colour.exterior')}
            value={exteriorColour}
            onChange={(e) => onChange({ exteriorColour: e.currentTarget.value })}
          />
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
            <NumberInput
              label={t('configurator.colour.surcharge')}
              value={exteriorColourSurcharge}
              onChange={(v) => onChange({ exteriorColourSurcharge: v === '' ? '' : String(v) })}
              decimalScale={2}
              fixedDecimalScale
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
            <NumberInput
              label={t('configurator.colour.surcharge')}
              value={interiorColourSurcharge}
              onChange={(v) => onChange({ interiorColourSurcharge: v === '' ? '' : String(v) })}
              decimalScale={2}
              fixedDecimalScale
              min={0}
            />
          )}
        </Group>
      </Stack>

      <Stack gap="xs">
        <Text fw={600} size="sm">
          {t('configurator.wheels.title')}
        </Text>

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
            <NumberInput
              label={t('configurator.colour.surcharge')}
              value={wheelsSurcharge}
              onChange={(v) => onChange({ wheelsSurcharge: v === '' ? '' : String(v) })}
              decimalScale={2}
              fixedDecimalScale
              min={0}
            />
          )}
        </Group>
      </Stack>
    </Stack>
  )
}
