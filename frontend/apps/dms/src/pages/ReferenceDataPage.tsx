import { useEffect, useMemo, useState, type ChangeEvent } from 'react'
import { useSearchParams } from 'react-router-dom'
import { Alert, Badge, Button, Card, Group, Select, Stack, Text, TextInput, Title, Tooltip } from '@mantine/core'
import { useDebouncedValue } from '@mantine/hooks'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import { CircleAlert, Check, Languages, Pencil, Plus, ShieldCheck } from 'lucide-react'
import { useTranslation } from 'react-i18next'
import {
  ColumnConfigPanel,
  DataGrid,
  FormDialog,
  defaultColumnLayout,
  semantic,
  slate,
  sortRowsInBrowser,
  useSetBreadcrumb,
  type GridColumnDef,
  type RowMenuGroups,
} from '@nexotec/ui-kit'
import { api, ApiError } from '../api/client'
import { useCanWrite } from '../auth/useCanWrite'
import { useColumnPanelLabels } from '../hooks/useColumnPanelLabels'
import { useDataGridLabels } from '../hooks/useDataGridLabels'
import { useGridPreferences } from '../hooks/useGridPreferences'
import { useIdempotencyKey } from '../hooks/useIdempotencyKey'
import { useSortInUrl } from '../hooks/useSortInUrl'
import { useUiPreferencesContext } from '../hooks/UiPreferencesContext'
import { apiRefusalText, refusedField } from '../utils/apiRefusal'
import type { ReferenceValueRead } from '../api/types'
import { REFERENCE_LIST_CODES, type ReferenceListCode } from '../referenceLists'
import { LANGUAGE_ENDONYMS, LANGUAGE_ORDER } from '../i18n/languageEndonyms'
import { SUPPORTED_LANGUAGES, toSwissLocale, type SupportedLanguage } from '../i18n'
import { fetchAllReferenceValues } from '../hooks/useReferenceValueOptions'
import { columnRegistryOf } from '../utils/columnRegistry'
import { formatDateTime } from '../utils/format'
import {
  LANGUAGE_FIELDS,
  deriveReferenceView,
  referenceRowLanguageErrors,
  referenceRowMatchesQuery,
} from '../utils/referenceData'

// KAN-59: the endpoint caps a page at 100 (Settings.pagination_max_limit) —
// `country` alone is ~250 rows, so a single `limit=200` request 422s. This
// admin screen's whole point is "see every value in this list to edit it,"
// so it walks the cursor rather than showing one truncated page, and never
// passes `active` — it manages inactive values too, not just what a
// dropdown would offer.

const GRID_KEY = 'platform.referenceData.values'

export function ReferenceDataPage() {
  const { t, i18n } = useTranslation()
  // KAN-471, KAN-514: the reference lists are the PRD's "Reference lists"
  // row (Platform Administration › Reference data; split from the canonical
  // taxonomy's row) — any role reads, platform staff alone write. Everyone else reads
  // them here and is offered neither «Neuer Wert» nor an inline edit
  // (KAN-430: hide the action, never the data). The API's own gate on the
  // POST and the PATCH stays the authority.
  const canWrite = useCanWrite('reference_lists')
  // A reader is told a language is missing, not that the row cannot be saved.
  const missingTip = canWrite ? t('referenceData.missingTip') : t('referenceData.missingTipReadOnly')
  useSetBreadcrumb([t('referenceData.breadcrumb'), t('referenceData.title')])
  const queryClient = useQueryClient()
  const [searchParams, setSearchParams] = useSearchParams()
  const locale = toSwissLocale((SUPPORTED_LANGUAGES as readonly string[]).includes(i18n.language) ? (i18n.language as SupportedLanguage) : 'de')
  const { density } = useUiPreferencesContext()
  const labels = useDataGridLabels()
  const columnPanelLabels = useColumnPanelLabels()
  const gridPrefs = useGridPreferences(GRID_KEY, { sort: [] })
  // KAN-516 (Anto, 2026-11-13, option C): a reference list is short and the
  // endpoint takes no sort, so the grid sorts in the browser; the sort is in
  // the URL (ADR-056). No sort keeps the list's own order.
  const [sort, setSort] = useSortInUrl(gridPrefs)

  // ADR-056 — the shareable grid state (which list, the filter, the sort)
  // lives in the URL; density / layout are the reader's own ergonomics and
  // stay out.
  const { list } = deriveReferenceView(searchParams)

  const [query, setQuery] = useState(() => deriveReferenceView(searchParams).query)
  const [debouncedQuery] = useDebouncedValue(query, 200)

  const updateUrl = (patch: Record<string, string | null>) => {
    setSearchParams(
      (prev) => {
        const next = new URLSearchParams(prev)
        for (const [key, value] of Object.entries(patch)) {
          if (value === null) next.delete(key)
          else next.set(key, value)
        }
        return next
      },
      { replace: true },
    )
  }

  useEffect(() => {
    updateUrl({ q: debouncedQuery || null })
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [debouncedQuery])

  const queryKey = useMemo(() => ['reference-data', list] as const, [list])
  const { data, isLoading, isError, error, refetch } = useQuery({
    queryKey,
    queryFn: () => fetchAllReferenceValues(list),
  })

  const forbidden = error instanceof ApiError && error.status === 403

  const rows = useMemo(() => data ?? [], [data])
  const visibleRows = useMemo(
    () => (debouncedQuery ? rows.filter((r) => referenceRowMatchesQuery(r, debouncedQuery)) : rows),
    [rows, debouncedQuery],
  )


  // `null` creates a value; a value edits its labels; `undefined` is closed.
  const [dialogValue, setDialogValue] = useState<ReferenceValueRead | null | undefined>(undefined)

  const listOptions = useMemo(() => REFERENCE_LIST_CODES.map((code) => ({ value: code, label: code })), [])

  const columns: GridColumnDef<ReferenceValueRead>[] = useMemo(
    () => [
      {
        id: 'code',
        header: t('referenceData.columns.code'),
        cell: ({ row }) => row.original.valueCode,
        meta: { locked: true, mono: true, width: 160, sortField: 'code', sortValue: (value) => value.valueCode },
      },
      ...LANGUAGE_FIELDS.map(
        (field, index): GridColumnDef<ReferenceValueRead> => ({
          id: field,
          // KAN-343: each language named in itself, whatever the UI language.
          header: LANGUAGE_ENDONYMS[LANGUAGE_ORDER[index]],
          cell: ({ row }) => {
            const value = row.original
            const empty = referenceRowLanguageErrors(value).has(field)
            // KAN-516: a grid row has a fixed height, so a cell edits nothing — an inline
            // refusal and its «Neu laden» would be clipped. Labels are edited in the row
            // menu's «Bezeichnungen bearbeiten» dialog (staff), which shows the refusal.
            return (
              <Text span size="sm" fs={empty ? 'italic' : undefined} c={empty ? slate[3] : undefined}>
                {empty ? t('referenceData.required') : value[field]}
              </Text>
            )
          },
          meta: { sortField: field, sortValue: (value) => value[field] },
        }),
      ),
      {
        id: 'status',
        header: '',
        cell: ({ row }) =>
          referenceRowLanguageErrors(row.original).size > 0 ? (
            <Tooltip label={missingTip} position="left" withArrow>
              <CircleAlert size={16} color={semantic.destructive.text} aria-label={missingTip} />
            </Tooltip>
          ) : (
            <Check size={16} color={semantic.success.text} aria-hidden="true" />
          ),
        meta: {
          width: 64,
          columnLabel: t('referenceData.columns.status'),
          sortField: 'status',
          sortValue: (value) => referenceRowLanguageErrors(value).size,
        },
      },
      {
        id: 'active',
        header: t('referenceData.columns.active'),
        cell: ({ row }) => (row.original.active ? t('common.yes') : t('common.no')),
        meta: { defaultVisible: false, sortField: 'active', sortValue: (value) => value.active },
      },
      {
        id: 'sortOrder',
        header: t('referenceData.columns.sortOrder'),
        cell: ({ row }) => row.original.sortOrder,
        meta: { align: 'right', defaultVisible: false, sortField: 'sortOrder', sortValue: (value) => value.sortOrder },
      },
      {
        id: 'createdAt',
        header: t('referenceData.columns.createdAt'),
        cell: ({ row }) => formatDateTime(row.original.createdAt, locale),
        meta: { align: 'right', defaultVisible: false, sortField: 'createdAt', sortValue: (value) => value.createdAt },
      },
      {
        id: 'updatedAt',
        header: t('referenceData.columns.updatedAt'),
        cell: ({ row }) => formatDateTime(row.original.updatedAt, locale),
        meta: { align: 'right', defaultVisible: false, sortField: 'updatedAt', sortValue: (value) => value.updatedAt },
      },
    ],
    [t, missingTip, locale],
  )
  const columnRegistry = useMemo(() => columnRegistryOf(columns), [columns])
  const columnLayout = useMemo(() => gridPrefs.columnLayout ?? defaultColumnLayout(columnRegistry), [gridPrefs.columnLayout, columnRegistry])
  const gridRows = useMemo(() => sortRowsInBrowser(visibleRows, sort, columns, locale), [visibleRows, sort, columns, locale])

  // ADR-061: the one definition of what can be done to a value — its labels,
  // for platform staff (KAN-430: a reader is offered no action, so no menu).
  const rowMenu = (value: ReferenceValueRead): RowMenuGroups => ({
    edit: [{ label: t('referenceData.edit.action'), icon: <Pencil size={16} />, onClick: () => setDialogValue(value) }],
  })

  return (
    <Stack gap="lg">
      <Group justify="space-between" align="flex-start">
        <Stack gap={2}>
          <Title order={2}>{t('referenceData.title')}</Title>
          <Text c="dimmed" size="sm">
            {t('referenceData.subtitle')}
          </Text>
        </Stack>
        {/* KAN-515: who maintains the lists, in the UI language — never a role code. */}
        <Badge color="grape" variant="light" leftSection={<ShieldCheck size={14} />}>
          {t('referenceData.maintainedBy')}
        </Badge>
      </Group>

      {forbidden && (
        <Alert color="yellow" icon={<ShieldCheck size={16} />} title={t('referenceData.forbidden.title')}>
          {t('referenceData.forbidden.body')}
        </Alert>
      )}

      {/* ── Card 1: the reference list ──────────────────────────────── */}
      <Card withBorder padding="lg" radius="md">
        <Stack gap="md">
          <Group gap="sm" wrap="nowrap" align="flex-start">
            <Languages size={18} />
            <Stack gap={2} style={{ flex: 1 }}>
              <Text fw={600}>{t('referenceData.list.title', { code: list })}</Text>
              <Text c="dimmed" size="sm">
                {t('referenceData.list.help')}
              </Text>
            </Stack>
          </Group>

          <Group gap="sm" wrap="wrap">
            <Select
              aria-label={t('referenceData.list.pickLabel')}
              data={listOptions}
              value={list}
              onChange={(value) => value && updateUrl({ list: value, q: null })}
              searchable
              w={260}
            />
            <TextInput
              aria-label={t('referenceData.list.filterLabel')}
              placeholder={t('referenceData.list.filterPlaceholder')}
              value={query}
              onChange={(e) => setQuery(e.currentTarget.value)}
              w={260}
            />
            <Group gap="sm" ml="auto">
              {!canWrite && (
                <Text c="dimmed" size="sm">
                  {t('referenceData.readOnly')}
                </Text>
              )}
              <ColumnConfigPanel registry={columnRegistry} layout={columnLayout} onLayoutChange={gridPrefs.setColumnLayout} labels={columnPanelLabels} />
              {canWrite && (
                <Button variant="light" leftSection={<Plus size={16} />} onClick={() => setDialogValue(null)}>
                  {t('referenceData.create.trigger')}
                </Button>
              )}
            </Group>
          </Group>

          {isError && !forbidden && (
            <Alert color="red" title={t('referenceData.loadError')}>
              <Button size="xs" variant="default" mt="xs" onClick={() => void refetch()}>
                {t('common.retry')}
              </Button>
            </Alert>
          )}

          <DataGrid<ReferenceValueRead>
            ariaLabel={t('referenceData.gridLabel')}
            columns={columns}
            rows={gridRows}
            getRowId={(value) => value.valueCode}
            sort={sort}
            onSortChange={setSort}
            density={density}
            loading={isLoading}
            fetchingNextPage={false}
            hasNextPage={false}
            onLoadMore={() => {}}
            total={gridRows.length}
            totalIsEstimate={false}
            isFiltered={Boolean(debouncedQuery)}
            labels={labels}
            emptyState={{ icon: <Languages size={24} />, title: t('referenceData.empty'), description: '' }}
            emptyFilteredState={{ icon: <Languages size={24} />, title: t('referenceData.emptyFiltered'), description: '' }}
            rowActions={canWrite ? rowMenu : undefined}
            rowTone={(value) => (referenceRowLanguageErrors(value).size > 0 ? 'destructive' : undefined)}
            columnLayout={columnLayout}
            onColumnLayoutChange={gridPrefs.setColumnLayout}
          />
        </Stack>
      </Card>

      {/* The mapping-gap queue moved to /catalogue?tab=mapping-gaps in C-B
          (KAN-40) — catalogue owns provider-code mapping, this screen owns
          canonical-vocabulary editing. */}

      {canWrite && (
        <ValueDialog
          opened={dialogValue !== undefined}
          editing={dialogValue ?? null}
          listCode={list}
          onClose={() => setDialogValue(undefined)}
          onSaved={(updated) => {
            setDialogValue(undefined)
            if (updated) {
              queryClient.setQueryData<ReferenceValueRead[]>(queryKey, (prev) =>
                prev ? prev.map((it) => (it.valueCode === updated.valueCode ? updated : it)) : prev,
              )
            } else void queryClient.invalidateQueries({ queryKey })
          }}
        />
      )}
    </Stack>
  )
}

/* Create and edit are a DIALOG (UI spec — Forms). Submit is disabled ONLY
   while the request is in flight — never for incompleteness; the server's
   422 comes back and is shown field by field. Editing (KAN-516, the row
   menu's «Bezeichnungen bearbeiten») shows the value code read-only and
   sends only the labels that changed, with If-Match on the value's version. */
function ValueDialog({
  opened,
  editing,
  listCode,
  onClose,
  onSaved,
}: {
  opened: boolean
  editing: ReferenceValueRead | null
  listCode: ReferenceListCode
  onClose: () => void
  /** The edited value, or `null` after a create (the list is read again). */
  onSaved: (updated: ReferenceValueRead | null) => void
}) {
  const { t } = useTranslation()
  const empty = { valueCode: '', labelDe: '', labelFr: '', labelIt: '', labelEn: '' }
  const [form, setForm] = useState(empty)
  const [submitting, setSubmitting] = useState(false)
  const [fieldErrors, setFieldErrors] = useState<Record<string, string>>({})
  const [formError, setFormError] = useState<string | null>(null)
  // KAN-119: a retried submit carries the same key, so the server replays the
  // first create instead of making a second value; reopening starts afresh.
  const idempotency = useIdempotencyKey()

  useEffect(() => {
    if (opened) {
      setForm(
        editing
          ? { valueCode: editing.valueCode, labelDe: editing.labelDe, labelFr: editing.labelFr, labelIt: editing.labelIt, labelEn: editing.labelEn }
          : empty,
      )
      setFieldErrors({})
      setFormError(null)
      idempotency.renew()
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [opened])

  const submit = async () => {
    setSubmitting(true)
    setFieldErrors({})
    setFormError(null)
    try {
      if (editing) {
        const changed = Object.fromEntries(LANGUAGE_FIELDS.filter((field) => form[field] !== editing[field]).map((field) => [field, form[field]]))
        if (Object.keys(changed).length === 0) {
          onClose()
          return
        }
        const updated = await api.patch<ReferenceValueRead>(`/reference-data/${listCode}/${editing.valueCode}`, changed, {
          'If-Match': String(editing.version),
        })
        onSaved(updated)
      } else {
        await api.post(`/reference-data/${listCode}`, { ...form }, idempotency.headers())
        idempotency.renew()
        onSaved(null)
      }
    } catch (err) {
      // KAN-437: said in the UI language — a schema 422 marks the field it
      // names, a value code the list already holds is a 409 naming it, a
      // stale If-Match is said as such.
      const field = err instanceof ApiError && err.status === 422 ? refusedField(err) : null
      if (field && field in empty) setFieldErrors({ [field]: t('referenceData.create.invalidField') })
      else if (!editing && err instanceof ApiError && err.status === 409 && typeof err.details?.valueCode === 'string') {
        setFormError(t('referenceData.create.duplicate', { valueCode: err.details.valueCode }))
      } else setFormError(apiRefusalText(t, err, editing ? 'referenceData.edit.error' : 'referenceData.create.error'))
    } finally {
      setSubmitting(false)
    }
  }

  // Read the value now: React runs a queued updater later, after the event
  // has cleared currentTarget (fast typing crashed the dialog).
  const set = (key: keyof typeof form) => (e: ChangeEvent<HTMLInputElement>) => {
    const value = e.currentTarget.value
    setForm((f) => ({ ...f, [key]: value }))
  }

  return (
    <FormDialog
      opened={opened}
      onClose={onClose}
      title={editing ? t('referenceData.edit.title', { valueCode: editing.valueCode, code: listCode }) : t('referenceData.create.title', { code: listCode })}
      submitLabel={editing ? t('referenceData.edit.submit') : t('referenceData.create.submit')}
      cancelLabel={t('common.cancel')}
      submitting={submitting}
      onSubmit={() => void submit()}
    >
      <Stack gap="sm">
        {formError && (
          <Alert color="red" variant="light">
            {formError}
          </Alert>
        )}
        <TextInput
          label={t('referenceData.columns.code')}
          placeholder="mild_hybrid"
          value={form.valueCode}
          onChange={set('valueCode')}
          error={fieldErrors.valueCode}
          data-autofocus={editing ? undefined : true}
          readOnly={editing !== null}
          required
        />
        <TextInput
          label={LANGUAGE_ENDONYMS.de}
          value={form.labelDe}
          onChange={set('labelDe')}
          error={fieldErrors.labelDe}
          data-autofocus={editing ? true : undefined}
          required
        />
        <TextInput label={LANGUAGE_ENDONYMS.fr} value={form.labelFr} onChange={set('labelFr')} error={fieldErrors.labelFr} required />
        <TextInput label={LANGUAGE_ENDONYMS.it} value={form.labelIt} onChange={set('labelIt')} error={fieldErrors.labelIt} required />
        <TextInput label={LANGUAGE_ENDONYMS.en} value={form.labelEn} onChange={set('labelEn')} error={fieldErrors.labelEn} required />
        <Text c="dimmed" size="xs">
          {t('referenceData.create.allLanguagesHint')}
        </Text>
      </Stack>
    </FormDialog>
  )
}
