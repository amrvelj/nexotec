import { useMemo } from 'react'
import { useTranslation } from 'react-i18next'
import { CheckCircle2, Download, FileArchive } from 'lucide-react'
import {
  Badge,
  DataGrid,
  defaultColumnLayout,
  sortRowsInBrowser,
  type BadgeTone,
  type GridColumnDef,
  type RowMenuGroups,
} from '@nexotec/ui-kit'
import type { ExportJobKind, ExportJobStatus, ExportRead } from '../../api/types'
import { useDataGridLabels } from '../../hooks/useDataGridLabels'
import { useGridPreferences } from '../../hooks/useGridPreferences'
import { useSortInUrl } from '../../hooks/useSortInUrl'
import { useUiPreferencesContext } from '../../hooks/UiPreferencesContext'
import { toSwissLocale, type SupportedLanguage } from '../../i18n'
import { columnRegistryOf } from '../../utils/columnRegistry'
import { formatDateTime } from '../../utils/format'
import { formatByteSize } from './formatByteSize'

const STATUS_TONE: Record<ExportJobStatus, BadgeTone> = {
  requested: 'informational',
  ready: 'success',
  failed: 'destructive',
  expired: 'slate',
}

export function ExportStatusBadge({ status }: { status: ExportJobStatus }) {
  const { t } = useTranslation()
  return (
    <Badge tone={STATUS_TONE[status]} dot>
      {t(`dataExport.status.${status}`)}
    </Badge>
  )
}

interface Props {
  gridKey: string
  ariaLabel: string
  exports: ExportRead[]
  loading: boolean
  /** The platform page shows the kind and staff's reason too. */
  showKind?: boolean
  /** Undefined: the row offers no download (an export that is not theirs). */
  onDownload?: (row: ExportRead) => void
  /** Only exports of this kind download here (staff hand over an archive export, never the manager's own). */
  downloadableKind?: ExportJobKind
  /** Undefined: the row offers no acknowledgement (no write, or not the manager's own). */
  onAcknowledge?: (row: ExportRead) => void
}

/**
 * KAN-218 — the exports of one Betrieb (Dealer Administration › Datenexport
 * and the platform's offboarding page): a short list, sorted in the browser
 * (ADR-158), the sort in the URL (ADR-056), one row menu per row (ADR-061).
 */
export function ExportsGrid({ gridKey, ariaLabel, exports, loading, showKind = false, onDownload, downloadableKind, onAcknowledge }: Props) {
  const { t, i18n } = useTranslation()
  const labels = useDataGridLabels()
  const locale = toSwissLocale(i18n.language as SupportedLanguage)
  const { density } = useUiPreferencesContext()
  const gridPrefs = useGridPreferences(gridKey, { sort: [{ field: 'createdAt', direction: 'desc' }] })
  const [sort, setSort] = useSortInUrl(gridPrefs, `${gridKey}Sort`)
  const columns: GridColumnDef<ExportRead>[] = useMemo(() => {
    const stamp = (value: string | null) => (value ? formatDateTime(value, locale) : '—')
    return [
      {
        id: 'createdAt',
        header: t('dataExport.columns.requestedAt'),
        cell: ({ row }) => formatDateTime(row.original.createdAt, locale),
        meta: { pinned: 'left', locked: true, width: 180, sortField: 'createdAt', sortValue: (row) => row.createdAt },
      },
      ...(showKind
        ? [
            {
              id: 'kind',
              header: t('dataExport.columns.kind'),
              cell: ({ row }: { row: { original: ExportRead } }) => t(`dataExport.kind.${row.original.kind}`),
              meta: { sortField: 'kind', sortValue: (row: ExportRead) => row.kind },
            } satisfies GridColumnDef<ExportRead>,
            {
              id: 'reason',
              header: t('dataExport.columns.reason'),
              cell: ({ row }: { row: { original: ExportRead } }) => row.original.reason ?? '—',
              meta: { width: 260 },
            } satisfies GridColumnDef<ExportRead>,
          ]
        : []),
      {
        id: 'status',
        header: t('dataExport.columns.status'),
        cell: ({ row }) => <ExportStatusBadge status={row.original.status} />,
        meta: { sortField: 'status', sortValue: (row) => row.status },
      },
      {
        id: 'producedAt',
        header: t('dataExport.columns.producedAt'),
        cell: ({ row }) => stamp(row.original.producedAt),
        meta: { sortField: 'producedAt', sortValue: (row) => row.producedAt ?? '' },
      },
      {
        id: 'downloadExpiresAt',
        header: t('dataExport.columns.downloadExpiresAt'),
        cell: ({ row }) => (row.original.status === 'ready' ? stamp(row.original.downloadExpiresAt) : '—'),
        meta: { sortField: 'downloadExpiresAt', sortValue: (row) => row.downloadExpiresAt ?? '' },
      },
      {
        id: 'byteSize',
        header: t('dataExport.columns.size'),
        cell: ({ row }) => formatByteSize(row.original.byteSize),
        meta: { align: 'right', sortField: 'byteSize', sortValue: (row) => row.byteSize ?? 0 },
      },
      {
        id: 'acknowledgedAt',
        header: t('dataExport.columns.acknowledgedAt'),
        cell: ({ row }) => stamp(row.original.acknowledgedAt),
        meta: { sortField: 'acknowledgedAt', sortValue: (row) => row.acknowledgedAt ?? '' },
      },
    ]
  }, [t, locale, showKind])
  const columnRegistry = useMemo(() => columnRegistryOf(columns), [columns])
  const columnLayout = useMemo(() => gridPrefs.columnLayout ?? defaultColumnLayout(columnRegistry), [gridPrefs.columnLayout, columnRegistry])
  const rows = useMemo(() => sortRowsInBrowser(exports, sort, columns, locale), [exports, sort, columns, locale])

  // ADR-061: the one definition of what can be done to an export from the list.
  const rowMenu = (row: ExportRead): RowMenuGroups => {
    const notReady = row.status !== 'ready' ? t(`dataExport.rowMenu.notReady.${row.status}`) : undefined
    const edit = []
    if (onDownload) {
      const why = downloadableKind && row.kind !== downloadableKind ? t('dataExport.rowMenu.managersOwn') : notReady
      edit.push({
        label: t('dataExport.rowMenu.download'),
        icon: <Download size={16} />,
        onClick: () => onDownload(row),
        disabled: Boolean(why),
        disabledReason: why,
      })
    }
    if (onAcknowledge && row.kind === 'self_service') {
      const why = row.acknowledgedAt ? t('dataExport.rowMenu.alreadyAcknowledged') : notReady
      edit.push({
        label: t('dataExport.rowMenu.acknowledge'),
        icon: <CheckCircle2 size={16} />,
        onClick: () => onAcknowledge(row),
        disabled: Boolean(why),
        disabledReason: why,
      })
    }
    return { edit }
  }

  return (
    <DataGrid<ExportRead>
      ariaLabel={ariaLabel}
      columns={columns}
      rows={rows}
      getRowId={(row) => row.id}
      sort={sort}
      onSortChange={setSort}
      density={density}
      loading={loading}
      fetchingNextPage={false}
      hasNextPage={false}
      onLoadMore={() => {}}
      total={rows.length}
      totalIsEstimate={false}
      isFiltered={false}
      labels={labels}
      emptyState={{ icon: <FileArchive size={24} />, title: t('dataExport.empty.title'), description: t('dataExport.empty.description') }}
      rowActions={rowMenu}
      columnLayout={columnLayout}
      onColumnLayoutChange={gridPrefs.setColumnLayout}
    />
  )
}
