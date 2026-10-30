import { useMemo, useState } from 'react'
import { Alert, Button, Checkbox, Group, Select, Stack, Switch, Table, Text } from '@mantine/core'
import { useQuery } from '@tanstack/react-query'
import { useTranslation } from 'react-i18next'
import { api, ApiError } from '../../api/client'
import type { MembershipList, MembershipRead } from '../../api/types'
import { useAuth } from '../../auth/AuthContext'
import { useIdempotencyKey } from '../../hooks/useIdempotencyKey'
import { toSwissLocale, type SupportedLanguage } from '../../i18n'
import { refusalText } from '../../userOptions'
import { formatDateTime } from '../../utils/format'

/**
 * KAN-407 — the user's access to sister Betriebe (Dealer Administration,
 * Users → Fields → Organisation), on the KAN-199 endpoints. Like the
 * connected accounts below it, a change here stands on its own: it is saved
 * at once, not with the form. The listing is the API's — a dealer manager
 * sees only the Betriebe they manage themselves — so an empty list never
 * claims the user has no other access, and a write the API answers with 404
 * (a Betrieb the manager does not manage) says so instead of failing
 * generically. Ending an access or setting its manager flag checks the
 * user's version (If-Match) and moves it on; `onUserVersion` hands the new
 * one to the form so its own save does not meet a stale version.
 */
export function UserMembershipsSection({
  dealershipId,
  userId,
  onUserVersion,
}: {
  dealershipId: string
  userId: string
  onUserVersion: (version: number) => void
}) {
  const { t, i18n } = useTranslation()
  const locale = toSwissLocale(i18n.language as SupportedLanguage)
  const { user: me, memberships: myBetriebe } = useAuth()
  const isPlatformStaff = me?.accessRoles.includes('platform_admin') ?? false
  const path = `/dealerships/${dealershipId}/users/${userId}/memberships`
  const listing = useQuery({
    queryKey: ['users', dealershipId, userId, 'memberships'],
    queryFn: () => api.get<MembershipList>(path),
  })
  const grantKey = useIdempotencyKey()
  const [target, setTarget] = useState<string | null>(null)
  const [targetIsManager, setTargetIsManager] = useState(false)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)

  const items = listing.data?.items ?? []
  const activeIds = new Set(items.filter((m) => m.active).map((m) => m.dealershipId))
  // The Betriebe the signed-in user works in themselves, beside this one and
  // those the user is already in: the API decides whether they manage them.
  const grantable = useMemo(
    () =>
      myBetriebe
        .filter((b) => b.id !== dealershipId && !activeIds.has(b.id))
        .map((b) => ({ value: b.id, label: b.legalName })),
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [myBetriebe, dealershipId, listing.data],
  )

  const run = async (action: () => Promise<unknown>) => {
    setBusy(true)
    setError(null)
    try {
      await action()
      const fresh = await listing.refetch()
      if (fresh.data) onUserVersion(fresh.data.userVersion)
    } catch (err) {
      setError(
        err instanceof ApiError && err.status === 404 ? t('usersAdmin.memberships.notYours') : refusalText(t, err, 'usersAdmin.memberships.failed'),
      )
      if (err instanceof ApiError && err.status === 409) void listing.refetch()
    } finally {
      setBusy(false)
    }
  }

  const grant = () =>
    run(async () => {
      const body = { dealershipId: target!, isDealerManager: targetIsManager }
      await api.post<MembershipRead>(path, body, grantKey.headers(body))
      grantKey.renew()
      setTarget(null)
      setTargetIsManager(false)
    })
  const version = () => ({ 'If-Match': String(listing.data!.userVersion) })
  const end = (m: MembershipRead) => run(() => api.delete<void>(`${path}/${m.dealershipId}`, version()))
  const setFlag = (m: MembershipRead, isDealerManager: boolean) =>
    run(() => api.put<MembershipRead>(`${path}/${m.dealershipId}/manager-flag`, { isDealerManager }, version()))

  if (listing.isError) return <Text c="dimmed" fz="sm">{t('common.requestFailed')}</Text>
  if (!listing.data) return null
  return (
    <Stack gap="xs">
      {error && (
        <Alert color="red" role="alert">
          {error}
        </Alert>
      )}
      {items.length === 0 ? (
        <Text c="dimmed" fz="sm">
          {isPlatformStaff ? t('usersAdmin.memberships.none') : t('usersAdmin.memberships.noneYouManage')}
        </Text>
      ) : (
        <Table aria-label={t('usersAdmin.memberships.tableLabel')}>
          <Table.Thead>
            <Table.Tr>
              <Table.Th>{t('usersAdmin.memberships.betrieb')}</Table.Th>
              <Table.Th>{t('usersAdmin.memberships.manager')}</Table.Th>
              <Table.Th>{t('usersAdmin.memberships.status')}</Table.Th>
              <Table.Th />
            </Table.Tr>
          </Table.Thead>
          <Table.Tbody>
            {items.map((m) => (
              <Table.Tr key={m.dealershipId}>
                <Table.Td>{m.dealershipLabel}</Table.Td>
                <Table.Td>
                  <Switch
                    aria-label={t('usersAdmin.memberships.managerIn', { betrieb: m.dealershipLabel })}
                    checked={m.isDealerManager}
                    disabled={!m.active || busy}
                    onChange={(e) => void setFlag(m, e.currentTarget.checked)}
                  />
                </Table.Td>
                <Table.Td>
                  {m.active
                    ? t('usersAdmin.memberships.active')
                    : t('usersAdmin.memberships.endedOn', { date: m.validTo ? formatDateTime(m.validTo, locale) : '—' })}
                </Table.Td>
                <Table.Td>
                  {m.active && (
                    <Button variant="subtle" size="xs" color="red" disabled={busy} onClick={() => void end(m)}>
                      {t('usersAdmin.memberships.end', { betrieb: m.dealershipLabel })}
                    </Button>
                  )}
                </Table.Td>
              </Table.Tr>
            ))}
          </Table.Tbody>
        </Table>
      )}
      {grantable.length > 0 && (
        <Group align="flex-end">
          <Select
            label={t('usersAdmin.memberships.grantTarget')}
            data={grantable}
            value={target}
            onChange={setTarget}
            placeholder={t('usersAdmin.fields.none')}
            clearable
          />
          <Checkbox
            mb={8}
            label={t('usersAdmin.memberships.grantAsManager')}
            checked={targetIsManager}
            onChange={(e) => setTargetIsManager(e.currentTarget.checked)}
          />
          <Button variant="light" disabled={!target || busy} onClick={() => void grant()}>
            {t('usersAdmin.memberships.grant')}
          </Button>
        </Group>
      )}
    </Stack>
  )
}
