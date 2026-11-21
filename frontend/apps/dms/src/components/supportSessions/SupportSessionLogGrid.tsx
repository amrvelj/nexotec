import { useMemo, useState } from 'react'
import { Alert } from '@mantine/core'
import { useInfiniteQuery } from '@tanstack/react-query'
import { useTranslation } from 'react-i18next'
import { Headset, ShieldCheck } from 'lucide-react'
import {
  Badge,
  DataGrid,
  defaultColumnLayout,
  sortSpecToQueryParam,
  type BadgeTone,
  type GridColumnDef,
  type SortSpec,
} from '@nexotec/ui-kit'
import { api, ApiError } from '../../api/client'
import type { SupportSessionLogEntry, SupportSessionLogPage } from '../../api/types'
import { useDataGridLabels } from '../../hooks/useDataGridLabels'
import { useGridPreferences } from '../../hooks/useGridPreferences'
import { useUiPreferencesContext } from '../../hooks/UiPreferencesContext'
import { toSwissLocale, type SupportedLanguage } from '../../i18n'
import { columnRegistryOf } from '../../utils/columnRegistry'
import { formatDateTime } from '../../utils/format'

type State = 'running' | 'staff' | 'user' | 'timeout'

const STATE_TONE: Record<State, BadgeTone> = {
  running: 'warning',
  staff: 'slate',
  user: 'slate',
  timeout: 'slate',
}

function stateOf(entry: SupportSessionLogEntry): State {
  return entry.active ? 'running' : (entry.endedBy ?? 'timeout')
}

// The log reads newest first (PR 5's endpoint pages oldest first without a sort).
const NEWEST_FIRST: SortSpec[] = [{ field: 'startedAt', direction: 'desc' }]

interface Props {
  gridKey: string
  /** `/dealerships/{id}/support-sessions` or `/platform/support-sessions[?staffUserId=…]`. */
  path: string
  /** The platform-wide view names the Betrieb of each session. */
  showDealership?: boolean
}

/**
 * KAN-221 PR-6 (FR-A-15) — the support-session log: one Betrieb's for its
 * manager, every Betrieb's (or one staff member's) for platform staff. Paged
 * and sorted on the server, newest first by default. The session's own
 * columns sort; the three names (read from other rows when asked, never
 * copied) and the derived state do not — the log's endpoint is not on the
 * shared list-query layer yet (KAN-461), and a sort of the loaded pages alone
 * would misorder the log.
 */
export function SupportSessionLogGrid({ gridKey, path, showDealership = false }: Props) {
  const { t, i18n } = useTranslation()
  const labels = useDataGridLabels()
  const locale = toSwissLocale(i18n.language as SupportedLanguage)
  const { density } = useUiPreferencesContext()
  const gridPrefs = useGridPreferences(gridKey, { sort: NEWEST_FIRST })
  const [sort, setSort] = useState<SortSpec[]>(NEWEST_FIRST)
  const sortParam = sortSpecToQueryParam(sort) ?? 'startedAt:desc'
  const separator = path.includes('?') ? '&' : '?'
  const log = useInfiniteQuery({
    queryKey: ['support-sessions', path, sortParam],
    queryFn: ({ pageParam }) =>
      api.get<SupportSessionLogPage>(
        `${path}${separator}sort=${encodeURIComponent(sortParam)}${pageParam ? `&cursor=${encodeURIComponent(pageParam)}` : ''}`,
      ),
    initialPageParam: null as string | null,
    getNextPageParam: (last) => last.nextCursor ?? null,
    retry: false,
  })
  const rows = useMemo(() => log.data?.pages.flatMap((page) => page.items) ?? [], [log.data])
  const forbidden = log.error instanceof ApiError && log.error.status === 403

  const columns: GridColumnDef<SupportSessionLogEntry>[] = useMemo(() => {
    const stamp = (value: string | null | undefined) => (value ? formatDateTime(value, locale) : '—')
    return [
      {
        id: 'startedAt',
        header: t('supportSessions.log.columns.startedAt'),
        cell: ({ row }) => formatDateTime(row.original.startedAt, locale),
        meta: { pinned: 'left', locked: true, width: 170, sortField: 'startedAt' },
      },
      {
        id: 'state',
        header: t('supportSessions.log.columns.state'),
        cell: ({ row }) => {
          const state = stateOf(row.original)
          return (
            <Badge tone={STATE_TONE[state]} dot>
              {t(`supportSessions.log.state.${state}`)}
            </Badge>
          )
        },
        meta: { width: 190 },
      },
      {
        id: 'staff',
        header: t('supportSessions.log.columns.staff'),
        cell: ({ row }) => row.original.staffName,
        meta: { width: 180 },
      },
      {
        id: 'user',
        header: t('supportSessions.log.columns.user'),
        cell: ({ row }) => row.original.targetUserName,
        meta: { width: 180 },
      },
      ...(showDealership
        ? [
            {
              id: 'dealership',
              header: t('supportSessions.log.columns.dealership'),
              cell: ({ row }: { row: { original: SupportSessionLogEntry } }) => row.original.dealershipName,
              meta: { width: 200 },
            } satisfies GridColumnDef<SupportSessionLogEntry>,
          ]
        : []),
      {
        id: 'reason',
        header: t('supportSessions.log.columns.reason'),
        cell: ({ row }) => row.original.reason,
        meta: { width: 320, sortField: 'reason' },
      },
      {
        id: 'expiresAt',
        header: t('supportSessions.log.columns.expiresAt'),
        cell: ({ row }) => stamp(row.original.expiresAt),
        meta: { width: 170, sortField: 'expiresAt' },
      },
      {
        id: 'endedAt',
        header: t('supportSessions.log.columns.endedAt'),
        cell: ({ row }) => stamp(row.original.endedAt),
        meta: { width: 170, sortField: 'endedAt' },
      },
    ]
  }, [t, locale, showDealership])
  const columnRegistry = useMemo(() => columnRegistryOf(columns), [columns])
  const columnLayout = useMemo(() => gridPrefs.columnLayout ?? defaultColumnLayout(columnRegistry), [gridPrefs.columnLayout, columnRegistry])

  if (forbidden) {
    return (
      <Alert color="yellow" icon={<ShieldCheck size={16} />} title={t('supportSessions.log.forbidden.title')}>
        {t('supportSessions.log.forbidden.body')}
      </Alert>
    )
  }
  return (
    <>
      {log.error && !forbidden && (
        <Alert color="red" role="alert" mb="sm">
          {t('supportSessions.log.loadError')}
        </Alert>
      )}
      <DataGrid<SupportSessionLogEntry>
        ariaLabel={t('supportSessions.log.ariaLabel')}
        columns={columns}
        rows={rows}
        getRowId={(row) => row.id}
        sort={sort}
        onSortChange={(next) => setSort(next.length > 0 ? next : NEWEST_FIRST)}
        density={density}
        loading={log.isLoading}
        fetchingNextPage={log.isFetchingNextPage}
        hasNextPage={Boolean(log.hasNextPage)}
        onLoadMore={() => void log.fetchNextPage()}
        total={log.hasNextPage ? null : rows.length}
        totalIsEstimate={false}
        isFiltered={false}
        labels={labels}
        emptyState={{
          icon: <Headset size={24} />,
          title: t('supportSessions.log.empty.title'),
          description: t('supportSessions.log.empty.description'),
        }}
        columnLayout={columnLayout}
        onColumnLayoutChange={gridPrefs.setColumnLayout}
      />
    </>
  )
}
