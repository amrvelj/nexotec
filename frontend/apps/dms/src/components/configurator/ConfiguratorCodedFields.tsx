import { memo } from 'react'
import { Group, Select, Stack, Text, TextInput } from '@mantine/core'
import { useTranslation } from 'react-i18next'
import { useReferenceLabelLookup } from '../../hooks/useReferenceLabelLookup'
import { CODED_FIELDS, CODED_REF_LIST_CODES, type CodedFieldKey } from '../../configurationOptions'
import type { CodedValues } from './configuratorDraft'
import {
  codedOptionsOf,
  codedValueRetiredOf,
  type CodedFieldDef,
  type CodedValueRows,
  type LabelField,
} from './configuratorFieldRules'

interface CodedSelectProps {
  field: CodedFieldDef
  value: string | null
  codedValues: CodedValueRows | undefined
  labelField: LabelField
  onPick: (key: CodedFieldKey, value: string) => void
}

/** KAN-96 — one of a manual configuration's five coded fields. */
export const CodedSelect = memo(function CodedSelect({ field, value, codedValues, labelField, onPick }: CodedSelectProps) {
  const { t } = useTranslation()
  const error =
    codedValues?.[field.listCode] === null
      ? t('configurator.spec.listUnavailable')
      : codedValueRetiredOf(codedValues, field.listCode, value)
        ? t('configurator.spec.retired')
        : undefined
  return (
    <Select
      label={t(field.labelKey)}
      data={codedOptionsOf(codedValues, labelField, field.listCode, value)}
      value={value}
      onChange={(v) => {
        if (v != null) onPick(field.key, v)
      }}
      error={error}
      // Anto, 2026-10-12 — all five are required on a manual
      // configuration and none can be cleared (the API answers
      // an explicit null with 422 `coded_field_required`): a
      // field can be changed, never emptied.
      required
      clearable={false}
      allowDeselect={false}
      w={200}
    />
  )
})

/** A provider configuration's coded values, shown read-only — the same
 * shared lookup the summary card reads (KAN-260). Anto, 2026-10-12: they are
 * the catalogue's, never editable (the API refuses a change,
 * `coded_field_locked`); FR-C-16's re-sync is the one way they change. */
export const ProviderCodedFields = memo(function ProviderCodedFields({ coded }: { coded: CodedValues }) {
  const { t } = useTranslation()
  const codedLabels = useReferenceLabelLookup(CODED_REF_LIST_CODES)
  return (
    <Stack gap="xs">
      <Text fw={600} size="sm">
        {t('configurator.spec.groups.vehicleType')}
      </Text>
      <Text size="xs" c="dimmed">
        {t('configurator.spec.codedLocked')}
      </Text>
      <Group grow align="flex-start" wrap="wrap">
        {CODED_FIELDS.map((f) => {
          const code = coded[f.key]
          return (
            <TextInput
              key={f.key}
              label={t(f.labelKey)}
              value={code != null ? codedLabels.label(f.listCode, code) : '—'}
              readOnly
              variant="filled"
              w={200}
            />
          )
        })}
      </Group>
    </Stack>
  )
})
