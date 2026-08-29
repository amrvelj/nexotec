import { useState, type ReactNode } from 'react'
import { ActionIcon, Group, Radio, Select, Stack, Text } from '@mantine/core'
import { Plus, Trash2 } from 'lucide-react'
import { useTranslation } from 'react-i18next'
import { InlineEditField, KeyValueRow, purple, white } from '@nexotec/ui-kit'
import { ApiError } from '../../api/client'

export interface ContactPointRow<TType extends string> {
  id: string
  type: TType
  value: string
  isPrimary: boolean
}

interface ContactPointsEditorProps<TType extends string> {
  label: string
  addLabel: string
  typeOptions: { value: TType; label: string }[]
  rows: ContactPointRow<TType>[]
  newRowType: TType
  renderValueEditor: (value: string, onChange: (v: string) => void, autoFocus: boolean) => ReactNode
  onCreate: (row: { type: TType; value: string }) => Promise<void>
  onUpdate: (id: string, patch: { type?: TType; value?: string; isPrimary?: boolean }) => Promise<void>
  onDelete: (id: string) => Promise<void>
}

// FR-07: add/edit/delete phone numbers and email addresses independently
// of the main record, exactly one primary enforced. Unlike the customer
// record itself, these child rows have no version/If-Match column — "CTO
// ruling: is_primary isn't a high-contention field" — so there's no
// conflict/rollback handling here, just a plain error on failure.
export function ContactPointsEditor<TType extends string>({
  label,
  addLabel,
  typeOptions,
  rows,
  newRowType,
  renderValueEditor,
  onCreate,
  onUpdate,
  onDelete,
}: ContactPointsEditorProps<TType>) {
  const { t } = useTranslation()
  const errorMessage = (err: unknown): string => (err instanceof ApiError ? err.message : t('customerDetail.errors.somethingWentWrong'))
  const [adding, setAdding] = useState(false)
  const [draftType, setDraftType] = useState<TType>(newRowType)
  const [draftValue, setDraftValue] = useState('')
  const [addError, setAddError] = useState<string | null>(null)
  const [addSaving, setAddSaving] = useState(false)
  const [rowErrors, setRowErrors] = useState<Record<string, string>>({})
  const [deletingId, setDeletingId] = useState<string | null>(null)

  const startAdd = () => {
    setDraftType(newRowType)
    setDraftValue('')
    setAddError(null)
    setAdding(true)
  }

  const saveAdd = async () => {
    setAddSaving(true)
    setAddError(null)
    try {
      await onCreate({ type: draftType, value: draftValue })
      setAdding(false)
    } catch (err) {
      setAddError(errorMessage(err))
    } finally {
      setAddSaving(false)
    }
  }

  const handleDelete = async (id: string) => {
    setDeletingId(id)
    setRowErrors((prev) => ({ ...prev, [id]: '' }))
    try {
      await onDelete(id)
    } catch (err) {
      setRowErrors((prev) => ({ ...prev, [id]: errorMessage(err) }))
    } finally {
      setDeletingId(null)
    }
  }

  const setPrimary = async (id: string) => {
    setRowErrors((prev) => ({ ...prev, [id]: '' }))
    try {
      await onUpdate(id, { isPrimary: true })
    } catch (err) {
      setRowErrors((prev) => ({ ...prev, [id]: errorMessage(err) }))
    }
  }

  return (
    <Stack gap="xs">
      <Text size="sm" fw={600}>
        {label}
      </Text>
      {rows.length === 0 && !adding && (
        <Text size="sm" c="dimmed">
          {t('customerDetail.contactPoints.none')}
        </Text>
      )}
      <Radio.Group value={rows.find((r) => r.isPrimary)?.id ?? ''} onChange={setPrimary}>
        <Stack gap="xs">
          {rows.map((row) => (
            <div key={row.id}>
              <Group gap="xs" align="center" wrap="nowrap">
                <Radio value={row.id} label={t('customerDetail.contactPoints.primary')} styles={{ label: { whiteSpace: 'nowrap' } }} />
                <Select
                  data={typeOptions}
                  value={row.type}
                  onChange={(next) => next && void onUpdate(row.id, { type: next as TType })}
                  allowDeselect={false}
                  size="xs"
                  style={{ flex: '0 0 130px' }}
                />
                <div style={{ flex: 1 }}>
                  <InlineEditField
                    value={row.value}
                    onSave={(raw) => onUpdate(row.id, { value: raw })}
                    renderEditor={({ value, onChange, autoFocus }) => renderValueEditor(value, onChange, autoFocus)}
                  />
                </div>
                <ActionIcon
                  variant="subtle"
                  color="gray"
                  onClick={() => void handleDelete(row.id)}
                  loading={deletingId === row.id}
                  aria-label={t('customerDetail.contactPoints.removeAriaLabel', { label })}
                >
                  <Trash2 size={16} />
                </ActionIcon>
              </Group>
              {rowErrors[row.id] && (
                <Text size="xs" c="red" ml={36}>
                  {rowErrors[row.id]}
                </Text>
              )}
            </div>
          ))}
        </Stack>
      </Radio.Group>

      {adding ? (
        <KeyValueRow label={t('customerDetail.contactPoints.new')}>
          <Group gap="xs" justify="flex-end" wrap="nowrap">
            <Select
              data={typeOptions}
              value={draftType}
              onChange={(next) => next && setDraftType(next as TType)}
              allowDeselect={false}
              size="xs"
              style={{ flex: '0 0 130px' }}
            />
            <div style={{ flex: 1, textAlign: 'left' }}>{renderValueEditor(draftValue, setDraftValue, true)}</div>
          </Group>
        </KeyValueRow>
      ) : (
        <Group gap="xs" mt={2}>
          <button
            type="button"
            onClick={startAdd}
            style={{
              display: 'inline-flex',
              alignItems: 'center',
              gap: 4,
              fontSize: 12,
              fontWeight: 600,
              color: purple[6],
              background: 'none',
              border: 'none',
              cursor: 'pointer',
              padding: 0,
            }}
          >
            <Plus size={14} />
            {addLabel}
          </button>
        </Group>
      )}
      {adding && (
        <Group gap="xs">
          {addError && (
            <Text size="xs" c="red">
              {addError}
            </Text>
          )}
          <Group gap="xs" ml="auto">
            <button
              type="button"
              onClick={() => void saveAdd()}
              disabled={addSaving || !draftValue}
              style={{ fontSize: 12, fontWeight: 600, color: white, backgroundColor: purple[6], border: 'none', borderRadius: 6, padding: '4px 10px', cursor: 'pointer' }}
            >
              {t('customerDetail.contactPoints.save')}
            </button>
            <button
              type="button"
              onClick={() => setAdding(false)}
              disabled={addSaving}
              style={{ fontSize: 12, fontWeight: 600, background: 'none', border: 'none', cursor: 'pointer' }}
            >
              {t('customerDetail.contactPoints.cancel')}
            </button>
          </Group>
        </Group>
      )}
    </Stack>
  )
}
