import { useMemo, useState } from 'react'
import { Link2, Plus, Trash2 } from 'lucide-react'
import { Group, Menu, TextInput } from '@mantine/core'
import { DataGrid, InlineEditField, purple, type GridColumnDef } from '@nexotec/ui-kit'
import { useUiPreferencesContext } from '../../hooks/UiPreferencesContext'
import { formatDate } from '../../utils/format'
import { ApiError } from '../../api/client'
import type { CustomerExternalIdRead } from '../../api/types'

function errorMessage(err: unknown): string {
  return err instanceof ApiError ? err.message : 'Something went wrong.'
}

interface ExternalIdsTabProps {
  externalIds: CustomerExternalIdRead[]
  loading: boolean
  error: string | null
  // Write access is platform_admin-only (Customer PRD, D-08) — every other
  // role sees the same list read-only, no add/edit/delete affordances at all.
  canWrite: boolean
  onCreate: (row: { systemName: string; externalId: string }) => Promise<void>
  onUpdate: (id: string, patch: { systemName?: string; externalId?: string }) => Promise<void>
  onDelete: (id: string) => Promise<void>
}

export function ExternalIdsTab({ externalIds, loading, error, canWrite, onCreate, onUpdate, onDelete }: ExternalIdsTabProps) {
  const { density } = useUiPreferencesContext()
  const [adding, setAdding] = useState(false)
  const [draftSystem, setDraftSystem] = useState('')
  const [draftExternalId, setDraftExternalId] = useState('')
  const [addError, setAddError] = useState<string | null>(null)
  const [addSaving, setAddSaving] = useState(false)
  const [deletingId, setDeletingId] = useState<string | null>(null)

  const startAdd = () => {
    setDraftSystem('')
    setDraftExternalId('')
    setAddError(null)
    setAdding(true)
  }

  const saveAdd = async () => {
    setAddSaving(true)
    setAddError(null)
    try {
      await onCreate({ systemName: draftSystem, externalId: draftExternalId })
      setAdding(false)
    } catch (err) {
      setAddError(errorMessage(err))
    } finally {
      setAddSaving(false)
    }
  }

  const handleDelete = async (id: string) => {
    setDeletingId(id)
    try {
      await onDelete(id)
    } finally {
      setDeletingId(null)
    }
  }

  const columns: GridColumnDef<CustomerExternalIdRead>[] = useMemo(
    () => [
      {
        id: 'systemName',
        header: 'System',
        cell: ({ row }) =>
          canWrite ? (
            <InlineEditField value={row.original.systemName} onSave={(raw) => onUpdate(row.original.id, { systemName: raw })} />
          ) : (
            row.original.systemName
          ),
      },
      {
        id: 'externalId',
        header: 'External ID',
        cell: ({ row }) =>
          canWrite ? (
            <InlineEditField value={row.original.externalId} onSave={(raw) => onUpdate(row.original.id, { externalId: raw })} />
          ) : (
            row.original.externalId
          ),
        meta: { mono: true },
      },
      { id: 'createdAt', header: 'Linked', cell: ({ row }) => formatDate(row.original.createdAt), meta: { align: 'right' } },
    ],
    [canWrite, onUpdate]
  )

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 12 }}>
      <DataGrid<CustomerExternalIdRead>
        columns={columns}
        rows={externalIds}
        getRowId={(row) => row.id}
        sort={[]}
        onSortChange={() => {}}
        density={density}
        loading={loading}
        fetchingNextPage={false}
        hasNextPage={false}
        onLoadMore={() => {}}
        error={error}
        total={externalIds.length}
        totalIsEstimate={false}
        isFiltered={false}
        emptyState={{ icon: <Link2 size={24} />, title: 'No external IDs', description: 'No CRM/OEM system linkage on file for this customer.' }}
        rowActions={
          canWrite
            ? (row) => (
                <Menu.Item
                  leftSection={<Trash2 size={16} />}
                  color="red"
                  onClick={() => void handleDelete(row.id)}
                  disabled={deletingId === row.id}
                >
                  Remove
                </Menu.Item>
              )
            : undefined
        }
      />

      {canWrite &&
        (adding ? (
          <Group gap="xs" align="flex-end" wrap="nowrap">
            <TextInput label="System" value={draftSystem} onChange={(e) => setDraftSystem(e.currentTarget.value)} autoFocus style={{ flex: 1 }} />
            <TextInput label="External ID" value={draftExternalId} onChange={(e) => setDraftExternalId(e.currentTarget.value)} style={{ flex: 1 }} />
            <button
              type="button"
              onClick={() => void saveAdd()}
              disabled={addSaving || !draftSystem || !draftExternalId}
              style={{ fontSize: 12, fontWeight: 600, color: '#fff', backgroundColor: purple[6], border: 'none', borderRadius: 6, padding: '8px 12px', cursor: 'pointer' }}
            >
              Save
            </button>
            <button
              type="button"
              onClick={() => setAdding(false)}
              disabled={addSaving}
              style={{ fontSize: 12, fontWeight: 600, background: 'none', border: 'none', cursor: 'pointer', padding: '8px 4px' }}
            >
              Cancel
            </button>
          </Group>
        ) : (
          <button
            type="button"
            onClick={startAdd}
            style={{
              display: 'inline-flex',
              alignItems: 'center',
              gap: 4,
              alignSelf: 'flex-start',
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
            Add external ID
          </button>
        ))}
      {addError && (
        <span style={{ fontSize: 12, color: '#c92a2a' }}>{addError}</span>
      )}
    </div>
  )
}
