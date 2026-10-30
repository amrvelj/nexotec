import { useEffect, useMemo, useState } from 'react'
import { useSearchParams } from 'react-router-dom'
import { Alert, Button, Stack, TextInput } from '@mantine/core'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import { Pencil, Plus, Tags } from 'lucide-react'
import { useTranslation } from 'react-i18next'
import { ActionBar, DataGrid, FormDialog, type GridColumnDef, type RowMenuGroups, type SortSpec } from '@nexotec/ui-kit'
import { api } from '../../api/client'
import type { BrandPage, BrandRead } from '../../api/types'
import { useActionBarLabels } from '../../hooks/useActionBarLabels'
import { useDataGridLabels } from '../../hooks/useDataGridLabels'
import { useIdempotencyKey } from '../../hooks/useIdempotencyKey'
import { useUiPreferencesContext } from '../../hooks/UiPreferencesContext'
import { toSwissLocale, type SupportedLanguage } from '../../i18n'
import { apiRefusalText } from '../../utils/apiRefusal'
import { formatDateTime } from '../../utils/format'

const QUERY_KEY = ['vehicle-mdm', 'brands', 'admin']

/** ADR-061: the one definition of what can be done to a brand. Brands have
 * no detail screen, so the grid's row menu is its only surface. */
export function brandRowMenu(t: (key: string) => string, onEdit: () => void): RowMenuGroups {
  return { edit: [{ label: t('brandsAdmin.edit'), icon: <Pencil size={16} />, onClick: onEdit }] }
}

/**
 * KAN-448 — brand create and edit on the Catalogue page (`?tab=brands`),
 * against `GET/POST /v1/vehicle-mdm/brands` and `PATCH …/{id}`. Brands are
 * canonical, platform-wide reference data (ADR-075: variant master data is
 * global), so the write is platform staff's (`require_access_role()`), and
 * the host page shows this tab to them only. A brand's code is its key and
 * is set once; the name can be edited (If-Match on its version). A refusal
 * is said in the UI language (KAN-398).
 *
 * No server-side sort: the list endpoint takes none, so no header carries a
 * sort affordance (the mapping-gap queue beside it is built the same way).
 */
export function BrandsAdmin() {
  const { t, i18n } = useTranslation()
  const locale = toSwissLocale(i18n.language as SupportedLanguage)
  const dataGridLabels = useDataGridLabels()
  const actionBarLabels = useActionBarLabels()
  const { density, setDensity } = useUiPreferencesContext()
  const queryClient = useQueryClient()
  // ADR-056: the search is grid state, so it lives in the URL (`?brandsQ=`).
  const [searchParams, setSearchParams] = useSearchParams()
  const [query, setQuery] = useState(() => searchParams.get('brandsQ') ?? '')
  useEffect(() => {
    setSearchParams(
      (prev) => {
        const next = new URLSearchParams(prev)
        if (query.trim()) next.set('brandsQ', query)
        else next.delete('brandsQ')
        return next
      },
      { replace: true },
    )
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [query])
  const [sort, setSort] = useState<SortSpec[]>([])
  // `null` creates a brand; a brand edits that one; `undefined` is closed.
  const [editing, setEditing] = useState<BrandRead | null | undefined>(undefined)
  const [code, setCode] = useState('')
  const [displayName, setDisplayName] = useState('')
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const createKey = useIdempotencyKey()

  // Every brand, page by page: the endpoint takes no search, and a few
  // hundred brands at most is one screen's worth — the search below then
  // finds a brand whether or not it was on the first page.
  const { data, isLoading, isError, refetch, isRefetching } = useQuery({
    queryKey: QUERY_KEY,
    queryFn: async () => {
      const brands: BrandRead[] = []
      let cursor: string | null = null
      do {
        const params = new URLSearchParams({ limit: '100' })
        if (cursor) params.set('cursor', cursor)
        const page: BrandPage = await api.get<BrandPage>(`/vehicle-mdm/brands?${params.toString()}`)
        brands.push(...page.items)
        cursor = page.nextCursor
      } while (cursor)
      return brands
    },
  })
  const allRows = useMemo(() => data ?? [], [data])
  const needle = query.trim().toLocaleLowerCase()
  const rows = useMemo(
    () => (needle ? allRows.filter((b) => b.code.toLocaleLowerCase().includes(needle) || b.displayName.toLocaleLowerCase().includes(needle)) : allRows),
    [allRows, needle],
  )

  const open = (brand: BrandRead | null) => {
    setEditing(brand)
    setCode(brand?.code ?? '')
    setDisplayName(brand?.displayName ?? '')
    setError(null)
    if (brand === null) createKey.renew()
  }

  const save = async () => {
    if (editing === undefined) return
    setSaving(true)
    setError(null)
    try {
      if (editing === null) {
        const body = { code: code.trim(), displayName: displayName.trim() }
        await api.post<BrandRead>('/vehicle-mdm/brands', body, createKey.headers(body))
        createKey.renew()
      } else {
        await api.patch<BrandRead>(`/vehicle-mdm/brands/${editing.id}`, { displayName: displayName.trim() }, { 'If-Match': String(editing.version) })
      }
      setEditing(undefined)
      await queryClient.invalidateQueries({ queryKey: QUERY_KEY })
    } catch (err) {
      setError(apiRefusalText(t, err, 'brandsAdmin.saveFailed'))
    } finally {
      setSaving(false)
    }
  }

  const columns: GridColumnDef<BrandRead>[] = useMemo(
    () => [
      { id: 'displayName', header: t('brandsAdmin.columns.displayName'), cell: ({ row }) => row.original.displayName },
      {
        id: 'code',
        header: t('brandsAdmin.columns.code'),
        cell: ({ row }) => <span style={{ fontFamily: 'monospace' }}>{row.original.code}</span>,
      },
      {
        id: 'updatedAt',
        header: t('brandsAdmin.columns.updatedAt'),
        cell: ({ row }) => formatDateTime(row.original.updatedAt, locale),
        meta: { align: 'right' },
      },
      {
        id: 'createdAt',
        header: t('brandsAdmin.columns.createdAt'),
        cell: ({ row }) => formatDateTime(row.original.createdAt, locale),
        meta: { align: 'right' },
      },
    ],
    [t, locale],
  )

  const incomplete = displayName.trim() === '' || (editing === null && code.trim() === '')

  return (
    <Stack gap="md">
      <ActionBar
        searchValue={query}
        onSearchChange={setQuery}
        searchPlaceholder={t('brandsAdmin.searchPlaceholder')}
        density={density}
        onDensityChange={setDensity}
        onRefresh={() => refetch()}
        refreshing={isRefetching}
        extraActions={
          <Button leftSection={<Plus size={16} />} onClick={() => open(null)}>
            {t('brandsAdmin.create')}
          </Button>
        }
        labels={actionBarLabels}
      />

      <DataGrid<BrandRead>
        columns={columns}
        rows={rows}
        getRowId={(row) => row.id}
        sort={sort}
        onSortChange={setSort}
        density={density}
        loading={isLoading}
        fetchingNextPage={false}
        hasNextPage={false}
        onLoadMore={() => {}}
        error={isError ? t('brandsAdmin.loadError') : null}
        onRetry={() => refetch()}
        total={null}
        totalIsEstimate={false}
        isFiltered={needle.length > 0}
        labels={dataGridLabels}
        rowActions={(row) => brandRowMenu(t, () => open(row))}
        emptyState={{ icon: <Tags size={24} />, title: t('brandsAdmin.emptyState.title'), description: t('brandsAdmin.emptyState.description') }}
        emptyFilteredState={{
          icon: <Tags size={24} />,
          title: t('brandsAdmin.emptyFilteredState.title'),
          description: t('brandsAdmin.emptyFilteredState.description'),
          action: (
            <Button variant="default" onClick={() => setQuery('')}>
              {t('brandsAdmin.emptyFilteredState.action')}
            </Button>
          ),
        }}
      />

      <FormDialog
        opened={editing !== undefined}
        onClose={() => setEditing(undefined)}
        title={editing ? t('brandsAdmin.editTitle') : t('brandsAdmin.createTitle')}
        onSubmit={save}
        submitLabel={t('common.save')}
        cancelLabel={t('common.cancel')}
        submitting={saving}
        submitDisabled={incomplete}
      >
        <Stack gap="md">
          <TextInput
            label={t('brandsAdmin.fields.code')}
            value={code}
            onChange={(e) => setCode(e.currentTarget.value)}
            maxLength={64}
            required={editing === null}
            readOnly={editing !== null && editing !== undefined}
            data-autofocus={editing === null ? true : undefined}
          />
          <TextInput
            label={t('brandsAdmin.fields.displayName')}
            value={displayName}
            onChange={(e) => setDisplayName(e.currentTarget.value)}
            maxLength={120}
            required
          />
          {error && (
            <Alert color="red" role="alert">
              {error}
            </Alert>
          )}
        </Stack>
      </FormDialog>
    </Stack>
  )
}
