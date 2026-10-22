import { useMemo, useState } from 'react'
import { useSearchParams } from 'react-router-dom'
import { Alert, Button, Group, Select, Stack, Text, TextInput, Title } from '@mantine/core'
import { useQuery } from '@tanstack/react-query'
import { LifeBuoy, Search, ShieldCheck } from 'lucide-react'
import { useTranslation } from 'react-i18next'
import { Badge, DataGrid, useSetBreadcrumb, type GridColumnDef, type SortSpec } from '@nexotec/ui-kit'
import { api, ApiError } from '../api/client'
import type { DealershipPage, RecoveryResult, RecoveryUserList, RecoveryUserRead } from '../api/types'
import { useAuth } from '../auth/AuthContext'
import { RecoveryReasonDialog } from '../components/recovery/RecoveryReasonDialog'
import { buildRecoveryRowMenu, type RecoveryChoice } from '../components/recovery/recoveryRowMenu'
import { useUiPreferencesContext } from '../hooks/UiPreferencesContext'
import { toSwissLocale, type SupportedLanguage } from '../i18n'
import { displayUserStatus, userDisplayName, userStatusLabel, userStatusTone } from '../userOptions'
import { formatDateTime, formatNumber } from '../utils/format'

const RECOVERY_KEY = ['platform', 'recovery', 'users'] as const

type SortableField = 'name' | 'betrieb' | 'status'

function sortRows(rows: RecoveryUserRead[], sort: SortSpec[]): RecoveryUserRead[] {
  const [first] = sort
  if (!first) return rows
  const value = (row: RecoveryUserRead): string => {
    switch (first.field as SortableField) {
      case 'betrieb':
        return row.dealershipName
      case 'status':
        return row.status
      default:
        return `${row.lastName} ${row.firstName}`
    }
  }
  const sorted = [...rows].sort((a, b) => value(a).localeCompare(value(b)))
  return first.direction === 'desc' ? sorted.reverse() : sorted
}

/**
 * KAN-202 — Administration › Account recovery (Authentication & Identity
 * FR-AU-25…27, D-A-16). Platform staff find the account they were asked
 * about — by its exact e-mail address or by Betrieb — and act on it from the
 * row menu, each action with a stated reason the Betrieb's manager will read.
 * The API is the control: anyone but staff with a second factor in the
 * session gets a 403, said on screen; the nav entry is never hidden
 * (A-RULE-7). The search lives in the URL (ADR-056). The whole result is
 * loaded (at most 200 accounts), so sorting it here sorts all of it.
 */
export function AccountRecoveryPage() {
  const { t, i18n } = useTranslation()
  const locale = toSwissLocale(i18n.language as SupportedLanguage)
  useSetBreadcrumb([t('shell.nav.administration'), t('shell.nav.accountRecovery')])
  const { user: me } = useAuth()
  const { density } = useUiPreferencesContext()
  const [searchParams, setSearchParams] = useSearchParams()
  const email = searchParams.get('email') ?? ''
  const dealershipId = searchParams.get('dealershipId') ?? ''
  const [emailDraft, setEmailDraft] = useState(email)
  const [dealershipDraft, setDealershipDraft] = useState<string | null>(dealershipId || null)
  const [sort, setSort] = useState<SortSpec[]>([{ field: 'name', direction: 'asc' }])
  const [acting, setActing] = useState<{ user: RecoveryUserRead; choice: RecoveryChoice } | null>(null)
  const [notice, setNotice] = useState<string | null>(null)

  const searching = email.length > 0 || dealershipId.length > 0
  const results = useQuery({
    queryKey: [...RECOVERY_KEY, email, dealershipId],
    queryFn: () => {
      const params = new URLSearchParams()
      if (email) params.set('email', email)
      if (dealershipId) params.set('dealershipId', dealershipId)
      return api.get<RecoveryUserList>(`/platform/users?${params.toString()}`)
    },
    enabled: searching,
    retry: false,
  })
  const dealerships = useQuery({
    queryKey: ['platform', 'recovery', 'dealerships'],
    queryFn: () => api.get<DealershipPage>('/dealerships?limit=100'),
    retry: false,
  })
  const forbidden =
    (results.error instanceof ApiError && results.error.status === 403) ||
    (dealerships.error instanceof ApiError && dealerships.error.status === 403)

  const runSearch = () => {
    const next = new URLSearchParams()
    if (emailDraft.trim()) next.set('email', emailDraft.trim())
    if (dealershipDraft) next.set('dealershipId', dealershipDraft)
    setNotice(null)
    setSearchParams(next, { replace: true })
  }

  const dealershipOptions = useMemo(
    () => (dealerships.data?.items ?? []).map((d) => ({ value: d.id, label: d.tradeName || d.legalName })),
    [dealerships.data],
  )
  const rows = useMemo(() => sortRows(results.data?.items ?? [], sort), [results.data, sort])

  const columns: GridColumnDef<RecoveryUserRead>[] = useMemo(
    () => [
      {
        id: 'name',
        header: t('recovery.columns.name'),
        cell: ({ row }) => userDisplayName(row.original),
        meta: { pinned: 'left', locked: true, width: 300, sortField: 'name', secondary: (row) => row.email },
      },
      {
        id: 'betrieb',
        header: t('recovery.columns.betrieb'),
        cell: ({ row }) => row.original.dealershipName || '—',
        meta: { sortField: 'betrieb' },
      },
      {
        id: 'status',
        header: t('recovery.columns.status'),
        cell: ({ row }) => {
          const status = displayUserStatus(row.original.status, null)
          return (
            <Badge tone={userStatusTone(status)} dot>
              {userStatusLabel(t, status)}
            </Badge>
          )
        },
        meta: { sortField: 'status' },
      },
      {
        id: 'roles',
        header: t('recovery.columns.roles'),
        cell: ({ row }) => (
          <Group gap={4}>
            {row.original.isDealerManager && <Badge tone="purple">{t('usersAdmin.managerBadge')}</Badge>}
            {row.original.isPlatformStaff && <Badge tone="informational">{t('recovery.staffBadge')}</Badge>}
            {!row.original.isDealerManager && !row.original.isPlatformStaff && '—'}
          </Group>
        ),
      },
      {
        id: 'lockout',
        header: t('recovery.columns.lockout'),
        cell: ({ row }) =>
          row.original.lockedUntil
            ? t('recovery.lockedUntil', { when: formatDateTime(row.original.lockedUntil, locale) })
            : row.original.failedLoginAttempts > 0
              ? t('recovery.failedAttempts', { count: row.original.failedLoginAttempts })
              : '—',
      },
      {
        id: 'password',
        header: t('recovery.columns.password'),
        cell: ({ row }) => (row.original.hasPassword ? t('recovery.passwordSet') : t('recovery.passwordNone')),
      },
      {
        id: 'mfa',
        header: t('recovery.columns.mfa'),
        cell: ({ row }) => (row.original.mfaEnrolled ? t('recovery.mfaEnrolled') : t('recovery.mfaNone')),
      },
      {
        id: 'sessions',
        header: t('recovery.columns.sessions'),
        cell: ({ row }) => formatNumber(row.original.openSessions),
        meta: { align: 'right' },
      },
    ],
    [t, locale],
  )

  const onDone = (result: RecoveryResult) => {
    const name = acting ? userDisplayName(acting.user) : ''
    setNotice(t(`recovery.actions.${result.action}.done`, { name }))
    setActing(null)
    void results.refetch()
  }

  return (
    <Stack gap="md">
      <Title order={2}>{t('recovery.title')}</Title>
      <Text size="sm" c="dimmed">
        {t('recovery.intro')}
      </Text>

      {forbidden && (
        <Alert color="yellow" icon={<ShieldCheck size={16} />} title={t('recovery.forbidden.title')}>
          {t('recovery.forbidden.body')}
        </Alert>
      )}
      {notice && (
        <Alert color="green" role="status" withCloseButton closeButtonLabel={t('common.dismiss')} onClose={() => setNotice(null)}>
          {notice}
        </Alert>
      )}

      <form
        onSubmit={(e) => {
          e.preventDefault()
          runSearch()
        }}
      >
        <Group align="flex-end" gap="sm">
          <TextInput
            label={t('recovery.search.email')}
            placeholder={t('recovery.search.emailPlaceholder')}
            value={emailDraft}
            onChange={(e) => setEmailDraft(e.currentTarget.value)}
            type="email"
            w={320}
          />
          <Select
            label={t('recovery.search.betrieb')}
            placeholder={t('recovery.search.betriebPlaceholder')}
            data={dealershipOptions}
            value={dealershipDraft}
            onChange={setDealershipDraft}
            searchable
            clearable
            nothingFoundMessage={t('recovery.search.noBetrieb')}
            w={320}
          />
          <Button type="submit" leftSection={<Search size={16} />} disabled={!emailDraft.trim() && !dealershipDraft}>
            {t('recovery.search.submit')}
          </Button>
        </Group>
      </form>

      {searching && (
        <DataGrid<RecoveryUserRead>
          columns={columns}
          rows={rows}
          getRowId={(row) => row.id}
          sort={sort}
          onSortChange={setSort}
          density={density}
          loading={results.isLoading}
          refetching={results.isRefetching && !results.isLoading}
          fetchingNextPage={false}
          hasNextPage={false}
          onLoadMore={() => {}}
          error={results.isError && !forbidden ? t('recovery.loadError') : null}
          onRetry={() => void results.refetch()}
          total={rows.length}
          totalIsEstimate={false}
          isFiltered
          rowActions={(row) =>
            buildRecoveryRowMenu(t, row, (choice) => setActing({ user: row, choice }), { isSelf: row.id === me?.id })
          }
          labels={{
            showing: (count) => t('common.showing', { count }),
            showingOfTotal: (count, totalStr) => t('common.showingOfTotal', { count, total: totalStr }),
            loadingMore: t('common.loadingMore'),
            retry: t('common.retry'),
            rowActionsLabel: t('common.rowActionsLabel'),
          }}
          emptyState={{
            icon: <LifeBuoy size={24} />,
            title: t('recovery.empty.title'),
            description: t('recovery.empty.description'),
          }}
          emptyFilteredState={{
            icon: <LifeBuoy size={24} />,
            title: t('recovery.empty.title'),
            description: t('recovery.empty.description'),
          }}
        />
      )}

      <RecoveryReasonDialog
        opened={acting !== null}
        onClose={() => setActing(null)}
        user={acting?.user ?? null}
        choice={acting?.choice ?? null}
        onDone={onDone}
      />
    </Stack>
  )
}
