import { useEffect, useMemo, useState } from 'react'
import { useSearchParams } from 'react-router-dom'
import { Alert, Button, Group, Stack, Title } from '@mantine/core'
import { useDebouncedValue } from '@mantine/hooks'
import { useInfiniteQuery, useQuery, useQueryClient } from '@tanstack/react-query'
import { ShieldCheck, UserCog, UserPlus } from 'lucide-react'
import { useTranslation } from 'react-i18next'
import {
  ActionBar,
  Badge,
  ColumnConfigPanel,
  DataGrid,
  OverviewShellRegion,
  ViewsAndFilters,
  defaultColumnLayout,
  useSetBreadcrumb,
  type ColumnRegistryEntry,
  type GridColumnDef,
  type SortSpec,
} from '@nexotec/ui-kit'
import { api, ApiError } from '../api/client'
import type { LocationList, OwnershipTransfersRead, UserAdminRead, UserDeactivatedRead, UserPage, UserStatus, UserUpdate } from '../api/types'
import { useAuth } from '../auth/AuthContext'
import { UserDeactivateDialog } from '../components/users/UserDeactivateDialog'
import { UserFormDialog } from '../components/users/UserFormDialog'
import { buildUserRowMenu } from '../components/users/userRowMenu'
import { useColumnPanelLabels } from '../hooks/useColumnPanelLabels'
import { useViewsAndFiltersLabels } from '../hooks/useViewsAndFiltersLabels'
import { useGridPreferences } from '../hooks/useGridPreferences'
import { useIdempotencyKey } from '../hooks/useIdempotencyKey'
import { useSavedViews } from '../hooks/useSavedViews'
import { useUiPreferencesContext } from '../hooks/UiPreferencesContext'
import { toSwissLocale, type SupportedLanguage } from '../i18n'
import {
  USER_STATUS_VALUES,
  accessRoleLabel,
  displayUserStatus,
  employmentStatusLabel,
  ownedGroupLabel,
  refusalText,
  userDisplayName,
  userRoleLabel,
  userStatusLabel,
  userStatusTone,
} from '../userOptions'
import { formatDateOnly, formatDateTime, todayLocalIsoDate } from '../utils/format'
import { useActionBarLabels } from '../hooks/useActionBarLabels'

const GRID_KEY = 'administration.users.list'
const DEFAULT_SORT: SortSpec[] = [
  { field: 'lastName', direction: 'asc' },
  { field: 'firstName', direction: 'asc' },
]

function serializeSort(sort: SortSpec[]): string {
  return sort.map((s) => `${s.field}:${s.direction}`).join(',')
}

function parseSortParam(raw: string): SortSpec[] {
  return raw
    .split(',')
    .filter(Boolean)
    .map((part) => {
      const [field, direction] = part.split(':')
      return { field, direction: direction === 'desc' ? 'desc' : 'asc' } as SortSpec
    })
}

type Chip = 'all' | UserStatus

/**
 * KAN-200 — Dealer Administration › Users (FR-A-09…13, D-A-35): the one
 * screen a manager uses every week. The list is the ordinary overview
 * grid (ADR-056: search and the status chip live in the URL; ADR-060: every
 * stored field is a column, a documented subset visible); invite and edit
 * share one dialog; deactivation carries the reassignment step. Manager-
 * only at the API (`dealership_users`): a 403 is said on screen, the nav
 * entry is never hidden (A-RULE-7). There is no delete (A-RULE-5).
 */
export function UsersListPage() {
  const { t, i18n } = useTranslation()
  const locale = toSwissLocale(i18n.language as SupportedLanguage)
  useSetBreadcrumb([t('shell.nav.administration'), t('shell.nav.users')])
  const { user: me, activeDealership } = useAuth()
  const dealershipId = activeDealership?.id ?? ''
  const { density, setDensity } = useUiPreferencesContext()
  const gridPrefs = useGridPreferences(GRID_KEY, { sort: DEFAULT_SORT })
  const savedViews = useSavedViews(GRID_KEY)
  const columnPanelLabels = useColumnPanelLabels()
  const viewsAndFiltersLabels = useViewsAndFiltersLabels()
  const actionBarLabels = useActionBarLabels()
  const queryClient = useQueryClient()
  const [searchParams, setSearchParams] = useSearchParams()
  const chip = (searchParams.get('status') as Chip | null) ?? 'all'
  // ADR-056: the sort is part of the shareable URL; the grid preference is
  // the fallback when the URL carries none.
  const sort = searchParams.get('sort') ? parseSortParam(searchParams.get('sort')!) : gridPrefs.sort
  const sortParam = sort.length > 0 ? serializeSort(sort) : undefined

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

  const setSort = (next: SortSpec[]) => {
    gridPrefs.setSort(next)
    updateUrl({ sort: next.length > 0 ? serializeSort(next) : null })
  }

  const [query, setQuery] = useState(() => searchParams.get('q') ?? '')
  const [debouncedQuery] = useDebouncedValue(query, 250)
  useEffect(() => {
    updateUrl({ q: debouncedQuery || null })
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [debouncedQuery])

  const [appliedViewId, setAppliedViewId] = useState<string | null>(null)
  const [actionError, setActionError] = useState<string | null>(null)
  const [actionNotice, setActionNotice] = useState<string | null>(null)
  const [formUser, setFormUser] = useState<UserAdminRead | null | undefined>(undefined) // undefined = closed, null = invite
  // The reassignment dialog: deactivating a user, or handing over what a
  // (deactivated) user still owns — the retry after a module failed.
  const [reassigning, setReassigning] = useState<{ user: UserAdminRead; mode: 'deactivate' | 'transfer' } | null>(null)
  const resendKey = useIdempotencyKey()

  const usersKey = ['users', dealershipId] as const
  const { data, fetchNextPage, hasNextPage, isFetchingNextPage, isLoading, isError, error, refetch, isRefetching } =
    useInfiniteQuery({
      queryKey: [...usersKey, 'list', debouncedQuery, chip, sortParam],
      queryFn: async ({ pageParam }: { pageParam: string | null }) => {
        const params = new URLSearchParams()
        if (debouncedQuery) params.set('q', debouncedQuery)
        if (chip !== 'all') params.set('status', chip)
        if (sortParam) params.set('sort', sortParam)
        params.set('limit', '50')
        if (pageParam) params.set('cursor', pageParam)
        return api.get<UserPage>(`/dealerships/${dealershipId}/users?${params.toString()}`)
      },
      initialPageParam: null as string | null,
      getNextPageParam: (lastPage) => lastPage.nextCursor,
      enabled: dealershipId !== '',
    })
  const forbidden = error instanceof ApiError && error.status === 403

  // The pickers' data: the Betrieb's sites and its people (line manager,
  // the receivers of a leaver's records). Loaded once the list is allowed.
  const locations = useQuery({
    queryKey: [...usersKey, 'locations'],
    queryFn: () => api.get<LocationList>(`/dealerships/${dealershipId}/locations`),
    enabled: dealershipId !== '' && !forbidden,
  })
  const colleagues = useQuery({
    queryKey: [...usersKey, 'colleagues'],
    // Every person of the Betrieb, page by page: the line-manager picker and
    // the receivers of a leaver's records must not stop at the first page.
    queryFn: async () => {
      const items: UserAdminRead[] = []
      let cursor: string | null = null
      do {
        const page: UserPage = await api.get<UserPage>(
          `/dealerships/${dealershipId}/users?limit=100${cursor ? `&cursor=${encodeURIComponent(cursor)}` : ''}`,
        )
        items.push(...page.items)
        cursor = page.nextCursor ?? null
      } while (cursor)
      return { items }
    },
    enabled: dealershipId !== '' && !forbidden,
  })

  const rows = useMemo(() => data?.pages.flatMap((page) => page.items) ?? [], [data])
  const locationName = useMemo(() => {
    const byId = new Map((locations.data?.items ?? []).map((l) => [l.id, l.name]))
    return (id: string | null | undefined) => (id ? (byId.get(id) ?? '—') : '—')
  }, [locations.data])
  const colleagueName = useMemo(() => {
    const byId = new Map((colleagues.data?.items ?? []).map((c) => [c.id, userDisplayName(c)]))
    return (id: string | null | undefined) => (id ? (byId.get(id) ?? '—') : '—')
  }, [colleagues.data])

  const invalidate = () => queryClient.invalidateQueries({ queryKey: usersKey })

  const say = (err: unknown, fallback: string) => {
    // A refusal with a reason code (the last-manager rule) in the UI language.
    setActionError(err instanceof ApiError ? refusalText(t, err) : fallback)
    if (err instanceof ApiError && err.status === 409) void invalidate()
  }

  const resendInvitation = async (user: UserAdminRead) => {
    setActionError(null)
    try {
      const path = `/dealerships/${dealershipId}/users/${user.id}/invitations`
      await api.post(path, undefined, resendKey.headers([path]))
      resendKey.renew()
      await invalidate()
    } catch (err) {
      say(err, t('usersAdmin.actions.resendFailed'))
    }
  }

  const setStatus = async (user: UserAdminRead, status: 'active' | 'suspended') => {
    setActionError(null)
    try {
      // A reactivation after a termination brings the employment back too, and
      // one after an expired window clears it — the nightly job would otherwise
      // deactivate the person again that night (KAN-200 lifecycle).
      const expired = user.validTo !== null && user.validTo !== undefined && user.validTo < todayLocalIsoDate()
      const body: UserUpdate =
        status === 'active'
          ? {
              status,
              ...(user.employmentStatus === 'terminated' ? { employmentStatus: 'active' as const } : {}),
              ...(expired ? { validTo: null } : {}),
            }
          : { status }
      await api.patch<UserAdminRead>(`/dealerships/${dealershipId}/users/${user.id}`, body, { 'If-Match': String(user.version) })
      // FR-A-12: an end date is never dropped silently — the manager is told,
      // and sets the new one in the edit dialog.
      if (status === 'active' && expired) setActionNotice(t('usersAdmin.actions.validToCleared', { name: userDisplayName(user), date: formatDateOnly(user.validTo!, locale) }))
      await invalidate()
    } catch (err) {
      say(err, t('usersAdmin.actions.statusFailed'))
    }
  }

  const columns: GridColumnDef<UserAdminRead>[] = useMemo(() => {
    const text = (
      id: string,
      header: string,
      pick: (row: UserAdminRead) => string | null | undefined,
      defaultVisible = false,
      sortField?: string,
    ): GridColumnDef<UserAdminRead> => ({
      id,
      header,
      cell: ({ row }) => pick(row.original) || '—',
      meta: { defaultVisible, sortField, exportValue: (row) => pick(row) ?? '' },
    })
    return [
      {
        id: 'name',
        header: t('usersAdmin.columns.name'),
        cell: ({ row }) => userDisplayName(row.original),
        // Wide enough for a name beside its address at default density: the
        // pinned column's own minimum (120 px) squeezed the name out.
        meta: { pinned: 'left', locked: true, width: 300, sortField: 'lastName', secondary: (row) => row.email },
      },
      {
        id: 'status',
        header: t('usersAdmin.columns.status'),
        cell: ({ row }) => {
          const status = displayUserStatus(row.original.status, row.original.invitationExpiresAt)
          return (
            <Badge tone={userStatusTone(status)} dot>
              {userStatusLabel(t, status)}
            </Badge>
          )
        },
        meta: { sortField: 'status', exportValue: (row) => userStatusLabel(t, displayUserStatus(row.status, row.invitationExpiresAt)) },
      },
      text('jobTitle', t('usersAdmin.columns.jobTitle'), (row) => userRoleLabel(t, row.role), true, 'role'),
      {
        id: 'access',
        header: t('usersAdmin.columns.access'),
        cell: ({ row }) => {
          const [primary, ...rest] = row.original.accessRoles.filter((r) => r !== 'platform_admin')
          if (!primary) return '—'
          return rest.length > 0 ? `${accessRoleLabel(t, primary)} +${rest.length}` : accessRoleLabel(t, primary)
        },
        meta: { exportValue: (row) => row.accessRoles.map((r) => accessRoleLabel(t, r)).join(', ') },
      },
      {
        id: 'manager',
        header: t('usersAdmin.columns.manager'),
        cell: ({ row }) => (row.original.isDealerManager ? <Badge tone="purple">{t('usersAdmin.managerBadge')}</Badge> : '—'),
        meta: { sortField: 'isDealerManager', exportValue: (row) => (row.isDealerManager ? t('common.yes') : t('common.no')) },
      },
      text('primaryLocation', t('usersAdmin.columns.primaryLocation'), (row) => (row.primaryLocationId ? locationName(row.primaryLocationId) : null), true),
      {
        id: 'lastLogin',
        header: t('usersAdmin.columns.lastLogin'),
        cell: ({ row }) => (row.original.lastLoginAt ? formatDateTime(row.original.lastLoginAt, locale) : '—'),
        meta: { align: 'right', sortField: 'lastLoginAt', exportValue: (row) => (row.lastLoginAt ? formatDateTime(row.lastLoginAt, locale) : '') },
      },
      {
        id: 'validTo',
        header: t('usersAdmin.columns.validTo'),
        cell: ({ row }) => (row.original.validTo ? formatDateOnly(row.original.validTo, locale) : '—'),
        meta: { align: 'right', defaultVisible: false, sortField: 'validTo', exportValue: (row) => (row.validTo ? formatDateOnly(row.validTo, locale) : '') },
      },
      {
        id: 'validFrom',
        header: t('usersAdmin.columns.validFrom'),
        cell: ({ row }) => (row.original.validFrom ? formatDateOnly(row.original.validFrom, locale) : '—'),
        meta: { align: 'right', defaultVisible: false, sortField: 'validFrom', exportValue: (row) => (row.validFrom ? formatDateOnly(row.validFrom, locale) : '') },
      },
      text('email', t('usersAdmin.columns.email'), (row) => row.email, false, 'email'),
      text('mobile', t('usersAdmin.columns.mobile'), (row) => row.phone, false, 'phone'),
      text('directDial', t('usersAdmin.columns.directDial'), (row) => row.directDial, false, 'directDial'),
      text('department', t('usersAdmin.columns.department'), (row) => row.department, false, 'department'),
      text('employeeNumber', t('usersAdmin.columns.employeeNumber'), (row) => row.employeeNumber, false, 'employeeNumber'),
      text('lineManager', t('usersAdmin.columns.lineManager'), (row) => (row.lineManagerUserId ? colleagueName(row.lineManagerUserId) : null)),
      text('additionalLocations', t('usersAdmin.columns.additionalLocations'), (row) =>
        (row.additionalLocationIds ?? []).map(locationName).join(', '),
      ),
      text('employmentStatus', t('usersAdmin.columns.employmentStatus'), (row) => employmentStatusLabel(t, row.employmentStatus), false, 'employmentStatus'),
      text('salesAdvisorCode', t('usersAdmin.columns.salesAdvisorCode'), (row) => row.salesAdvisorCode, false, 'salesAdvisorCode'),
      text('commissionRelevant', t('usersAdmin.columns.commissionRelevant'), (row) => (row.commissionRelevant ? t('common.yes') : t('common.no')), false, 'commissionRelevant'),
      {
        id: 'invitedAt',
        header: t('usersAdmin.columns.invitedAt'),
        cell: ({ row }) => (row.original.invitedAt ? formatDateTime(row.original.invitedAt, locale) : '—'),
        meta: { align: 'right', defaultVisible: false, sortField: 'invitedAt', exportValue: (row) => (row.invitedAt ? formatDateTime(row.invitedAt, locale) : '') },
      },
      {
        id: 'deactivatedAt',
        header: t('usersAdmin.columns.deactivatedAt'),
        cell: ({ row }) => (row.original.deactivatedAt ? formatDateTime(row.original.deactivatedAt, locale) : '—'),
        meta: { align: 'right', defaultVisible: false, sortField: 'deactivatedAt', exportValue: (row) => (row.deactivatedAt ? formatDateTime(row.deactivatedAt, locale) : '') },
      },
      text('deactivationReason', t('usersAdmin.columns.deactivationReason'), (row) => row.deactivationReason, false, 'deactivationReason'),
      text('signature', t('usersAdmin.columns.signature'), (row) => (row.hasSignatureImage ? t('common.yes') : t('common.no'))),
      {
        id: 'createdAt',
        header: t('usersAdmin.columns.createdAt'),
        cell: ({ row }) => formatDateTime(row.original.createdAt, locale),
        meta: { align: 'right', defaultVisible: false, sortField: 'createdAt', exportValue: (row) => formatDateTime(row.createdAt, locale) },
      },
      {
        id: 'updatedAt',
        header: t('usersAdmin.columns.updatedAt'),
        cell: ({ row }) => formatDateTime(row.original.updatedAt, locale),
        meta: { align: 'right', defaultVisible: false, sortField: 'updatedAt', exportValue: (row) => formatDateTime(row.updatedAt, locale) },
      },
    ]
  }, [t, locale, locationName, colleagueName])

  const columnRegistry: ColumnRegistryEntry[] = useMemo(
    () =>
      columns.map((c) => ({
        id: String(c.id),
        label: typeof c.header === 'string' ? c.header : String(c.id),
        defaultVisible: c.meta?.defaultVisible ?? true,
        locked: c.meta?.locked,
      })),
    [columns],
  )
  const columnLayout = useMemo(() => gridPrefs.columnLayout ?? defaultColumnLayout(columnRegistry), [gridPrefs.columnLayout, columnRegistry])

  const isFiltered = debouncedQuery.length > 0 || chip !== 'all'
  const currentViewName = (appliedViewId && savedViews.views.find((v) => v.id === appliedViewId)?.name) || t('usersAdmin.allView')

  const onSaved = async () => {
    setFormUser(undefined)
    await invalidate()
  }
  const onReassigned = async (result: UserDeactivatedRead | OwnershipTransfersRead, mode: 'deactivate' | 'transfer') => {
    setReassigning(null)
    // A module that failed leaves its records with the person: said here, with
    // the retry at hand (the row menu's hand-over). The reason is a stable
    // code (`module_failed`, or the module's own refusal), never an exception.
    if (result.failed && result.failed.length > 0) {
      const groups = result.failed
        .map((f) => `${ownedGroupLabel(t, f.module, f.group)} (${t([`usersAdmin.failureReason.${f.reason}`, 'usersAdmin.failureReason.module_failed'])})`)
        .join('; ')
      setActionError(t(mode === 'deactivate' ? 'usersAdmin.actions.handOverFailedAfterDeactivation' : 'usersAdmin.actions.handOverFailed', { groups }))
    }
    await invalidate()
  }

  return (
    <Stack gap="md">
      <Group justify="space-between">
        <Title order={2}>{t('usersAdmin.title')}</Title>
        <Button leftSection={<UserPlus size={16} />} onClick={() => setFormUser(null)} disabled={forbidden}>
          {t('usersAdmin.invite')}
        </Button>
      </Group>

      {forbidden && (
        <Alert color="yellow" icon={<ShieldCheck size={16} />} title={t('usersAdmin.forbidden.title')}>
          {t('usersAdmin.forbidden.body')}
        </Alert>
      )}
      {actionError && (
        <Alert color="red" role="alert" withCloseButton closeButtonLabel={t('common.dismiss')} onClose={() => setActionError(null)}>
          {actionError}
        </Alert>
      )}
      {actionNotice && (
        <Alert color="yellow" withCloseButton closeButtonLabel={t('common.dismiss')} onClose={() => setActionNotice(null)}>
          {actionNotice}
        </Alert>
      )}

      <Group gap="xs">
        {(['all', ...USER_STATUS_VALUES] as Chip[]).map((c) => (
          <Button key={c} variant={chip === c ? 'filled' : 'default'} size="xs" onClick={() => updateUrl({ status: c === 'all' ? null : c })}>
            {c === 'all' ? t('usersAdmin.chips.all') : userStatusLabel(t, c)}
          </Button>
        ))}
      </Group>

      <OverviewShellRegion
        header={<div />}
        actionBar={
          <ActionBar
            labels={actionBarLabels}
            searchValue={query}
            onSearchChange={setQuery}
            searchPlaceholder={t('usersAdmin.searchPlaceholder')}
            density={density}
            onDensityChange={setDensity}
            onRefresh={() => refetch()}
            refreshing={isRefetching}
            filterSlot={
              <ViewsAndFilters
                labels={viewsAndFiltersLabels}
                currentViewName={currentViewName}
                views={savedViews.views}
                onApplyView={(view) => {
                  setAppliedViewId(view.id)
                  if (view.snapshot.columnLayout) gridPrefs.setColumnLayout(view.snapshot.columnLayout)
                }}
                onSaveCurrentAsView={(name) => savedViews.saveView(name, { sort, columnLayout: gridPrefs.columnLayout ?? undefined, filters: [] })}
                onDeleteView={savedViews.deleteView}
                onSetDefaultView={savedViews.setDefaultView}
                fields={[]}
                predicates={[]}
                onPredicatesChange={() => {}}
                onResetFilters={() => {}}
              />
            }
            columnsSlot={<ColumnConfigPanel registry={columnRegistry} layout={columnLayout} onLayoutChange={gridPrefs.setColumnLayout} labels={columnPanelLabels} />}
          />
        }
      >
        <DataGrid<UserAdminRead>
          columns={columns}
          rows={rows}
          getRowId={(row) => row.id}
          sort={sort}
          onSortChange={setSort}
          density={density}
          // ADR-061: the row menu is the one definition — a deactivated user is
          // not edited (the API refuses; the menu says why), so a click does nothing.
          onRowActivate={(row) => {
            if (row.status !== 'deactivated') setFormUser(row)
          }}
          loading={isLoading}
          refetching={isRefetching && !isLoading}
          fetchingNextPage={isFetchingNextPage}
          hasNextPage={Boolean(hasNextPage)}
          onLoadMore={() => fetchNextPage()}
          error={isError && !forbidden ? t('usersAdmin.loadError') : null}
          onRetry={() => refetch()}
          total={null}
          totalIsEstimate={false}
          isFiltered={isFiltered}
          columnLayout={columnLayout}
          onColumnLayoutChange={gridPrefs.setColumnLayout}
          rowActions={(row) =>
            buildUserRowMenu(
              t,
              row,
              {
                onEdit: () => setFormUser(row),
                onResendInvitation: () => void resendInvitation(row),
                onSuspend: () => void setStatus(row, 'suspended'),
                onReactivate: () => void setStatus(row, 'active'),
                onHandOver: () => setReassigning({ user: row, mode: 'transfer' }),
                onDeactivate: () => setReassigning({ user: row, mode: 'deactivate' }),
              },
              { isSelf: row.id === me?.id },
            )
          }
          labels={{
            showing: (count) => t('common.showing', { count }),
            showingOfTotal: (count, totalStr) => t('common.showingOfTotal', { count, total: totalStr }),
            loadingMore: t('common.loadingMore'),
            retry: t('common.retry'),
            rowActionsLabel: t('common.rowActionsLabel'),
          }}
          emptyState={{
            icon: <UserCog size={24} />,
            title: t('usersAdmin.emptyState.title'),
            description: t('usersAdmin.emptyState.description'),
            action: (
              <Button onClick={() => setFormUser(null)} disabled={forbidden}>
                {t('usersAdmin.invite')}
              </Button>
            ),
          }}
          emptyFilteredState={{
            icon: <UserCog size={24} />,
            title: t('usersAdmin.emptyFilteredState.title'),
            description: t('usersAdmin.emptyFilteredState.description'),
            action: (
              <Button
                variant="default"
                onClick={() => {
                  setQuery('')
                  updateUrl({ q: null, status: null })
                }}
              >
                {t('usersAdmin.emptyFilteredState.action')}
              </Button>
            ),
          }}
        />
      </OverviewShellRegion>

      <UserFormDialog
        opened={formUser !== undefined}
        onClose={() => setFormUser(undefined)}
        dealershipId={dealershipId}
        user={formUser ?? null}
        locations={locations.data?.items ?? []}
        colleagues={colleagues.data?.items ?? []}
        selfId={me?.id ?? ''}
        onSaved={() => void onSaved()}
      />
      <UserDeactivateDialog
        opened={reassigning !== null}
        onClose={() => setReassigning(null)}
        dealershipId={dealershipId}
        user={reassigning?.user ?? null}
        mode={reassigning?.mode ?? 'deactivate'}
        colleagues={colleagues.data?.items ?? []}
        onDone={(result) => void onReassigned(result, reassigning?.mode ?? 'deactivate')}
      />
    </Stack>
  )
}
