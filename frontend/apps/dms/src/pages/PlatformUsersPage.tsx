import { useMemo, useState } from 'react'
import { useSearchParams } from 'react-router-dom'
import { Alert, Button, Group, Modal, Stack, Text, TextInput, Textarea, Title } from '@mantine/core'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useTranslation } from 'react-i18next'
import { Headset, ShieldCheck, ShieldUser, UserMinus, UserPlus } from 'lucide-react'
import {
  Badge,
  DataGrid,
  defaultColumnLayout,
  sortRowsInBrowser,
  useSetBreadcrumb,
  type GridColumnDef,
  type RowMenuGroups,
} from '@nexotec/ui-kit'
import { api, ApiError } from '../api/client'
import type { PlatformStaffEntry, PlatformStaffPage, PlatformStaffRevoked } from '../api/types'
import { useAuth } from '../auth/AuthContext'
import { SupportSessionLogGrid } from '../components/supportSessions/SupportSessionLogGrid'
import { useDataGridLabels } from '../hooks/useDataGridLabels'
import { useGridPreferences } from '../hooks/useGridPreferences'
import { useSortInUrl } from '../hooks/useSortInUrl'
import { useUiPreferencesContext } from '../hooks/UiPreferencesContext'
import { toSwissLocale, type SupportedLanguage } from '../i18n'
import { apiRefusalText } from '../utils/apiRefusal'
import { columnRegistryOf } from '../utils/columnRegistry'

const STAFF_KEY = ['platform', 'staff'] as const

/** Every holder, page by page — a short list (a handful of staff). */
async function allStaff(): Promise<PlatformStaffEntry[]> {
  const items: PlatformStaffEntry[] = []
  let cursor: string | null = null
  do {
    const page: PlatformStaffPage = await api.get<PlatformStaffPage>(
      `/platform/staff?limit=100${cursor ? `&cursor=${encodeURIComponent(cursor)}` : ''}`,
    )
    items.push(...page.items)
    cursor = page.nextCursor ?? null
  } while (cursor)
  return items
}

const fullName = (entry: PlatformStaffEntry) => `${entry.firstName} ${entry.lastName}`.trim()

/**
 * KAN-221 PR-6 — Platform Administration › Platform users (FR-A-15, D-A-05;
 * ADR-188): Nexotec staff with their second factor and running support
 * session, «Zugang erteilen» (a new account in the platform tenant, invited by
 * e-mail) and «Zugang entziehen» (with a reason); below, the support-session
 * log of every Betrieb, or of the staff member picked. Staff-only at the API,
 * grant and revoke with a second factor; the entry is never role-hidden.
 */
export function PlatformUsersPage() {
  const { t, i18n } = useTranslation()
  const locale = toSwissLocale(i18n.language as SupportedLanguage)
  useSetBreadcrumb([t('shell.nav.administration'), t('supportSessions.staff.title')])
  const { user } = useAuth()
  const labels = useDataGridLabels()
  const { density } = useUiPreferencesContext()
  const queryClient = useQueryClient()
  const [searchParams, setSearchParams] = useSearchParams()
  const staffUserId = searchParams.get('staffUserId') ?? ''
  const gridPrefs = useGridPreferences('platformStaff', { sort: [{ field: 'name', direction: 'asc' }] })
  const [sort, setSort] = useSortInUrl(gridPrefs, 'platformStaffSort')
  const [granting, setGranting] = useState(false)
  const [draft, setDraft] = useState({ firstName: '', lastName: '', email: '' })
  const [revoking, setRevoking] = useState<PlatformStaffEntry | null>(null)
  const [reason, setReason] = useState('')
  const [notice, setNotice] = useState<string | null>(null)
  const [error, setError] = useState<string | null>(null)

  const staff = useQuery({ queryKey: STAFF_KEY, queryFn: allStaff, retry: false })
  const forbidden = staff.error instanceof ApiError && staff.error.status === 403
  const refresh = () => void queryClient.invalidateQueries({ queryKey: STAFF_KEY })

  const grant = useMutation({
    mutationFn: () =>
      api.post<PlatformStaffEntry>('/platform/staff', {
        firstName: draft.firstName.trim(),
        lastName: draft.lastName.trim(),
        email: draft.email.trim(),
      }),
    onSuccess: (entry) => {
      setGranting(false)
      setDraft({ firstName: '', lastName: '', email: '' })
      setError(null)
      setNotice(t('supportSessions.staff.grantDialog.done', { email: entry.email }))
      refresh()
    },
    onError: (err) => setError(apiRefusalText(t, err, 'supportSessions.staff.grantDialog.error')),
  })
  const revoke = useMutation({
    mutationFn: (entry: PlatformStaffEntry) =>
      api.post<PlatformStaffRevoked>(`/platform/staff/${entry.id}/revoke`, { reason: reason.trim() }, { 'If-Match': String(entry.version) }),
    onSuccess: (_result, entry) => {
      setRevoking(null)
      setReason('')
      setError(null)
      setNotice(t('supportSessions.staff.revokeDialog.done', { name: fullName(entry) }))
      refresh()
    },
    onError: (err) => {
      setRevoking(null)
      setError(apiRefusalText(t, err, 'supportSessions.staff.revokeDialog.error'))
      refresh()
    },
  })

  const columns: GridColumnDef<PlatformStaffEntry>[] = useMemo(
    () => [
      {
        id: 'name',
        header: t('supportSessions.staff.columns.name'),
        cell: ({ row }) => fullName(row.original),
        meta: { pinned: 'left', locked: true, width: 200, sortField: 'name', sortValue: (row) => fullName(row) },
      },
      {
        id: 'email',
        header: t('supportSessions.staff.columns.email'),
        cell: ({ row }) => row.original.email,
        meta: { width: 240, sortField: 'email', sortValue: (row) => row.email },
      },
      {
        id: 'status',
        header: t('supportSessions.staff.columns.status'),
        cell: ({ row }) => (
          <Badge tone={row.original.status === 'active' ? 'success' : 'slate'} dot>
            {t(`usersAdmin.status.${row.original.status}`)}
          </Badge>
        ),
        meta: { width: 150, sortField: 'status', sortValue: (row) => t(`usersAdmin.status.${row.status}`) },
      },
      {
        id: 'home',
        header: t('supportSessions.staff.columns.home'),
        cell: ({ row }) =>
          row.original.inPlatformTenant ? (
            t('supportSessions.staff.home.platform')
          ) : (
            <Badge tone="warning">{t('supportSessions.staff.home.legacy', { name: row.original.homeDealershipName })}</Badge>
          ),
        meta: { width: 260, sortField: 'home', sortValue: (row) => (row.inPlatformTenant ? '' : row.homeDealershipName) },
      },
      {
        id: 'mfa',
        header: t('supportSessions.staff.columns.mfa'),
        cell: ({ row }) => (
          <Badge tone={row.original.mfaEnrolled ? 'success' : 'destructive'} dot>
            {t(row.original.mfaEnrolled ? 'supportSessions.staff.mfa.yes' : 'supportSessions.staff.mfa.no')}
          </Badge>
        ),
        meta: { width: 150, sortField: 'mfa', sortValue: (row) => row.mfaEnrolled },
      },
      {
        id: 'session',
        header: t('supportSessions.staff.columns.session'),
        cell: ({ row }) =>
          row.original.runningSupportSessionId ? (
            <Badge tone="warning" dot>
              {t('supportSessions.staff.session.running')}
            </Badge>
          ) : (
            '—'
          ),
        meta: { width: 170, sortField: 'session', sortValue: (row) => row.runningSupportSessionId !== null },
      },
    ],
    [t],
  )
  const columnRegistry = useMemo(() => columnRegistryOf(columns), [columns])
  const columnLayout = useMemo(() => gridPrefs.columnLayout ?? defaultColumnLayout(columnRegistry), [gridPrefs.columnLayout, columnRegistry])
  const rows = useMemo(() => sortRowsInBrowser(staff.data ?? [], sort, columns, locale), [staff.data, sort, columns, locale])
  const picked = (staff.data ?? []).find((entry) => entry.id === staffUserId)

  const pick = (id: string | null) => {
    const next = new URLSearchParams(searchParams)
    if (id) next.set('staffUserId', id)
    else next.delete('staffUserId')
    setSearchParams(next, { replace: true })
  }

  // ADR-061: the one definition of what can be done to a staff member from the list.
  const rowMenu = (row: PlatformStaffEntry): RowMenuGroups => {
    const why =
      row.id === user?.id
        ? t('supportSessions.staff.rowMenu.self')
        : row.status === 'deactivated'
          ? t('supportSessions.staff.rowMenu.deactivated')
          : undefined
    return {
      navigate: [{ label: t('supportSessions.staff.rowMenu.showSessions'), icon: <Headset size={16} />, onClick: () => pick(row.id) }],
      destructive: [
        {
          label: t('supportSessions.staff.rowMenu.revoke'),
          icon: <UserMinus size={16} />,
          onClick: () => {
            setReason('')
            setRevoking(row)
          },
          disabled: Boolean(why),
          disabledReason: why,
        },
      ],
    }
  }

  const draftComplete = draft.firstName.trim() !== '' && draft.lastName.trim() !== '' && draft.email.trim() !== ''

  return (
    <Stack gap="md">
      <Group justify="space-between" align="flex-start">
        <div>
          <Title order={2}>{t('supportSessions.staff.title')}</Title>
          <Text size="sm" c="dimmed">
            {t('supportSessions.staff.intro')}
          </Text>
        </div>
        {!forbidden && (
          <Button leftSection={<UserPlus size={16} />} onClick={() => setGranting(true)}>
            {t('supportSessions.staff.grant')}
          </Button>
        )}
      </Group>
      {forbidden && (
        <Alert color="yellow" icon={<ShieldCheck size={16} />} title={t('supportSessions.staff.forbidden.title')}>
          {t('supportSessions.staff.forbidden.body')}
        </Alert>
      )}
      {staff.error && !forbidden && (
        <Alert color="red" role="alert">
          {t('supportSessions.staff.loadError')}
        </Alert>
      )}
      {notice && (
        <Alert color="green" role="status" withCloseButton closeButtonLabel={t('common.dismiss')} onClose={() => setNotice(null)}>
          {notice}
        </Alert>
      )}
      {error && (
        <Alert color="red" role="alert" withCloseButton closeButtonLabel={t('common.dismiss')} onClose={() => setError(null)}>
          {error}
        </Alert>
      )}
      {!forbidden && (
        <>
          <div style={{ height: 320 }}>
            <DataGrid<PlatformStaffEntry>
              ariaLabel={t('supportSessions.staff.ariaLabel')}
              columns={columns}
              rows={rows}
              getRowId={(row) => row.id}
              sort={sort}
              onSortChange={setSort}
              density={density}
              loading={staff.isLoading}
              fetchingNextPage={false}
              hasNextPage={false}
              onLoadMore={() => {}}
              total={rows.length}
              totalIsEstimate={false}
              isFiltered={false}
              labels={labels}
              emptyState={{
                icon: <ShieldUser size={24} />,
                title: t('supportSessions.staff.empty.title'),
                description: t('supportSessions.staff.empty.description'),
              }}
              rowActions={rowMenu}
              columnLayout={columnLayout}
              onColumnLayoutChange={gridPrefs.setColumnLayout}
            />
          </div>
          <Group justify="space-between">
            <Title order={4}>
              {picked ? t('supportSessions.staff.sessionsOf', { name: fullName(picked) }) : t('supportSessions.log.platformTitle')}
            </Title>
            {picked && (
              <Button variant="subtle" onClick={() => pick(null)}>
                {t('supportSessions.staff.showAll')}
              </Button>
            )}
          </Group>
          <div style={{ height: 360 }}>
            <SupportSessionLogGrid
              gridKey="platformSupportSessionLog"
              path={picked ? `/platform/support-sessions?staffUserId=${picked.id}` : '/platform/support-sessions'}
              showDealership
            />
          </div>
        </>
      )}

      <Modal opened={granting} onClose={() => setGranting(false)} title={t('supportSessions.staff.grantDialog.title')} centered>
        <Stack gap="sm">
          <Text fz="sm">{t('supportSessions.staff.grantDialog.body')}</Text>
          <TextInput
            label={t('supportSessions.staff.grantDialog.firstName')}
            value={draft.firstName}
            onChange={(e) => setDraft({ ...draft, firstName: e.currentTarget.value })}
            required
          />
          <TextInput
            label={t('supportSessions.staff.grantDialog.lastName')}
            value={draft.lastName}
            onChange={(e) => setDraft({ ...draft, lastName: e.currentTarget.value })}
            required
          />
          <TextInput
            label={t('supportSessions.staff.grantDialog.email')}
            type="email"
            value={draft.email}
            onChange={(e) => setDraft({ ...draft, email: e.currentTarget.value })}
            required
          />
          <Group justify="flex-end">
            <Button variant="default" onClick={() => setGranting(false)}>
              {t('common.cancel')}
            </Button>
            <Button disabled={!draftComplete} loading={grant.isPending} onClick={() => grant.mutate()}>
              {t('supportSessions.staff.grantDialog.submit')}
            </Button>
          </Group>
        </Stack>
      </Modal>

      <Modal opened={revoking !== null} onClose={() => setRevoking(null)} title={t('supportSessions.staff.revokeDialog.title')} centered>
        {revoking && (
          <Stack gap="sm">
            <Text fz="sm">
              {revoking.inPlatformTenant
                ? t('supportSessions.staff.revokeDialog.bodyPlatform', { name: fullName(revoking) })
                : t('supportSessions.staff.revokeDialog.bodyLegacy', { name: fullName(revoking), home: revoking.homeDealershipName })}
            </Text>
            <Textarea
              label={t('supportSessions.staff.revokeDialog.reason')}
              placeholder={t('supportSessions.staff.revokeDialog.reasonPlaceholder')}
              value={reason}
              onChange={(e) => setReason(e.currentTarget.value)}
              autosize
              minRows={2}
              required
            />
            <Group justify="flex-end">
              <Button variant="default" onClick={() => setRevoking(null)}>
                {t('common.cancel')}
              </Button>
              <Button color="red" disabled={reason.trim() === ''} loading={revoke.isPending} onClick={() => revoke.mutate(revoking)}>
                {t('supportSessions.staff.revokeDialog.submit')}
              </Button>
            </Group>
          </Stack>
        )}
      </Modal>
    </Stack>
  )
}
