import { useMemo, useState } from 'react'
import { Link2, Plus, Trash2 } from 'lucide-react'
import { Group, Menu, TextInput } from '@mantine/core'
import { useTranslation } from 'react-i18next'
import { DataGrid, InlineEditField, purple, semantic, white, type GridColumnDef } from '@nexotec/ui-kit'
import { useUiPreferencesContext } from '../../hooks/UiPreferencesContext'
import { formatDate } from '../../utils/format'
import { ApiError } from '../../api/client'
import { dataGridLabels } from '../../utils/dataGridI18n'
import type { CustomerExternalIdRead } from '../../api/types'

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
  locale: string
}

export function ExternalIdsTab({ externalIds, loading, error, canWrite, onCreate, onUpdate, onDelete, locale }: ExternalIdsTabProps) {
  const { t } = useTranslation()
  const errorMessage = (err: unknown): string => (err instanceof ApiError ? err.message : t('customerDetail.errors.somethingWentWrong'))
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
        header: t('customerDetail.externalIds.columns.system'),
        cell: ({ row }) =>
          canWrite ? (
            <InlineEditField value={row.original.systemName} onSave={(raw) => onUpdate(row.original.id, { systemName: raw })} />
          ) : (
            row.original.systemName
          ),
      },
      {
        id: 'externalId',
        header: t('customerDetail.externalIds.columns.externalId'),
        cell: ({ row }) =>
          canWrite ? (
            <InlineEditField value={row.original.externalId} onSave={(raw) => onUpdate(row.original.id, { externalId: raw })} />
          ) : (
            row.original.externalId
          ),
        meta: { mono: true },
      },
      {
        id: 'createdAt',
        header: t('customerDetail.externalIds.columns.linked'),
        cell: ({ row }) => formatDate(row.original.createdAt, locale),
        meta: { align: 'right' },
      },
    ],
    [canWrite, onUpdate, t, locale]
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
        locale={locale}
        labels={dataGridLabels(t)}
        emptyState={{
          icon: <Link2 size={24} />,
          title: t('customerDetail.externalIds.emptyState.title'),
          description: t('customerDetail.externalIds.emptyState.description'),
        }}
        rowActions={
          canWrite
            ? (row) => (
                <Menu.Item
                  leftSection={<Trash2 size={16} />}
                  color="red"
                  onClick={() => void handleDelete(row.id)}
                  disabled={deletingId === row.id}
                >
                  {t('customerDetail.externalIds.remove')}
                </Menu.Item>
              )
            : undefined
        }
      />

      {canWrite &&
        (adding ? (
          <Group gap="xs" align="flex-end" wrap="nowrap">
            <TextInput
              label={t('customerDetail.externalIds.systemLabel')}
              value={draftSystem}
              onChange={(e) => setDraftSystem(e.currentTarget.value)}
              autoFocus
              style={{ flex: 1 }}
            />
            <TextInput
              label={t('customerDetail.externalIds.externalIdLabel')}
              value={draftExternalId}
              onChange={(e) => setDraftExternalId(e.currentTarget.value)}
              style={{ flex: 1 }}
            />
            <button
              type="button"
              onClick={() => void saveAdd()}
              disabled={addSaving || !draftSystem || !draftExternalId}
              style={{ fontSize: 12, fontWeight: 600, color: white, backgroundColor: purple[6], border: 'none', borderRadius: 6, padding: '8px 12px', cursor: 'pointer' }}
            >
              {t('customerDetail.externalIds.save')}
            </button>
            <button
              type="button"
              onClick={() => setAdding(false)}
              disabled={addSaving}
              style={{ fontSize: 12, fontWeight: 600, background: 'none', border: 'none', cursor: 'pointer', padding: '8px 4px' }}
            >
              {t('customerDetail.externalIds.cancel')}
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
            {t('customerDetail.externalIds.add')}
          </button>
        ))}
      {addError && (
        <span style={{ fontSize: 12, color: semantic.destructive.text }}>{addError}</span>
      )}
    </div>
  )
}
