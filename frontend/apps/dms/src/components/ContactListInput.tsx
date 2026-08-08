import type { ReactNode } from 'react'
import { ActionIcon, Button, Group, Radio, Select, Stack, Text } from '@mantine/core'
import { Plus, Trash2 } from 'lucide-react'

export interface ContactRow<TType extends string> {
  key: string
  type: TType
  value: string
  isPrimary: boolean
}

interface ContactListInputProps<TType extends string> {
  label: string
  addLabel: string
  typeOptions: { value: TType; label: string }[]
  rows: ContactRow<TType>[]
  onChange: (rows: ContactRow<TType>[]) => void
  newRowType: TType
  renderValue: (value: string, onValueChange: (next: string) => void) => ReactNode
}

// Shared editor for both the phone list and the email list (FR-07: "type
// and exactly one primary each"). Genuinely generic — the value editor is
// a slot (PhoneInput for phones, a plain TextInput for emails), so this
// isn't tied to either contact kind and can be reused wherever else the
// app needs a typed, single-primary multi-value list.
export function ContactListInput<TType extends string>({
  label,
  addLabel,
  typeOptions,
  rows,
  onChange,
  newRowType,
  renderValue,
}: ContactListInputProps<TType>) {
  const addRow = () => {
    onChange([...rows, { key: crypto.randomUUID(), type: newRowType, value: '', isPrimary: rows.length === 0 }])
  }

  const removeRow = (key: string) => {
    const next = rows.filter((r) => r.key !== key)
    if (next.length > 0 && !next.some((r) => r.isPrimary)) {
      next[0].isPrimary = true
    }
    onChange(next)
  }

  const setPrimary = (key: string) => {
    onChange(rows.map((r) => ({ ...r, isPrimary: r.key === key })))
  }

  const updateRow = (key: string, patch: Partial<ContactRow<TType>>) => {
    onChange(rows.map((r) => (r.key === key ? { ...r, ...patch } : r)))
  }

  return (
    <Stack gap="xs">
      <Text size="sm" fw={600}>
        {label}
      </Text>
      {rows.length === 0 && (
        <Text size="sm" c="dimmed">
          None added yet.
        </Text>
      )}
      <Radio.Group value={rows.find((r) => r.isPrimary)?.key ?? ''} onChange={setPrimary}>
        <Stack gap="sm">
          {rows.map((row) => (
            <Group key={row.key} gap="xs" align="flex-end" wrap="nowrap">
              <Radio value={row.key} label="Primary" mb={8} styles={{ label: { whiteSpace: 'nowrap' } }} />
              <Select
                label="Type"
                data={typeOptions}
                value={row.type}
                onChange={(next) => next && updateRow(row.key, { type: next as TType })}
                allowDeselect={false}
                style={{ flex: '0 0 140px' }}
              />
              <div style={{ flex: 1 }}>{renderValue(row.value, (next) => updateRow(row.key, { value: next }))}</div>
              <ActionIcon variant="subtle" color="gray" onClick={() => removeRow(row.key)} mb={4} aria-label={`Remove ${label.toLowerCase()}`}>
                <Trash2 size={16} />
              </ActionIcon>
            </Group>
          ))}
        </Stack>
      </Radio.Group>
      <Button variant="default" size="xs" leftSection={<Plus size={14} />} onClick={addRow} style={{ alignSelf: 'flex-start' }}>
        {addLabel}
      </Button>
    </Stack>
  )
}
