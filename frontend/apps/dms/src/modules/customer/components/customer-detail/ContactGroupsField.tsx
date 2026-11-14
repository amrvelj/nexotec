import { Group, MultiSelect, Stack, Text } from '@mantine/core'
import { useTranslation } from 'react-i18next'
import { Badge, KeyValueRow } from '@nexotec/ui-kit'
import type { CustomerUpdateInput } from '../../../../api/types'
import { useContactGroups } from '../../../../hooks/useContactGroups'

/**
 * KAN-466 — the customer's contact groups, edited inline (full replace, like
 * tags). The picker offers the groups the active Betrieb has switched on; a
 * group the customer carries that is not on here — switched off since,
 * withdrawn by Nexotec, or set by a sister Betrieb — is shown beside it,
 * read-only, and every save keeps it (the API refuses dropping it,
 * `contact_group_read_only`).
 */
export function ContactGroupsField({
  label,
  value,
  onSaveField,
}: {
  label: string
  value: string[]
  onSaveField: (patch: Partial<CustomerUpdateInput>) => Promise<unknown> | void
}) {
  const { t } = useTranslation()
  const groups = useContactGroups()
  const editable = value.filter((code) => groups.offered.has(code))
  const readOnly = value.filter((code) => !groups.offered.has(code))
  const data = groups.items
    .filter((item) => groups.offered.has(item.valueCode))
    .map((item) => ({ value: item.valueCode, label: groups.label(item.valueCode) }))

  return (
    <KeyValueRow label={label}>
      <Stack gap={4}>
        {readOnly.length > 0 && (
          <Group gap={4} title={t('contactGroups.field.readOnlyHint')}>
            {readOnly.map((code) => (
              <Badge key={code} tone="slate">
                {groups.label(code)}
              </Badge>
            ))}
          </Group>
        )}
        {groups.query.data && data.length === 0 ? (
          <Text fz="xs" c="dimmed">
            {t('contactGroups.field.noneOffered')}
          </Text>
        ) : (
          <MultiSelect
            size="xs"
            aria-label={label}
            data={data}
            value={editable}
            placeholder={editable.length === 0 ? t('contactGroups.field.placeholder') : undefined}
            onChange={(next) => void onSaveField({ contactGroups: [...readOnly, ...next] })}
            comboboxProps={{ withinPortal: true }}
          />
        )}
      </Stack>
    </KeyValueRow>
  )
}
