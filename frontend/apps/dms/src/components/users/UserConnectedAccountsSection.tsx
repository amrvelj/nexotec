import { useState } from 'react'
import { Alert, Stack, Text } from '@mantine/core'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useTranslation } from 'react-i18next'
import { api, ApiError } from '../../api/client'
import type { ConnectedAccountListRead, ConnectedAccountRead } from '../../api/types'
import { ConnectedAccountsTable } from '../account/ConnectedAccountsTable'

/**
 * The Users screen's view of a user's connected accounts (KAN-201; FR-AU-24,
 * D-A-15): the manager sees provider, account, permissions and status and
 * may revoke — linking is the user's own, in My account, and no content is
 * ever shown. A revoke stands on its own: it changes the grant, not the
 * user being edited.
 */
export function UserConnectedAccountsSection({ dealershipId, userId }: { dealershipId: string; userId: string }) {
  const { t } = useTranslation()
  const queryClient = useQueryClient()
  const [error, setError] = useState<string | null>(null)
  const key = ['users', dealershipId, userId, 'connected-accounts']
  const path = `/dealerships/${dealershipId}/users/${userId}/connected-accounts`
  const accounts = useQuery({ queryKey: key, queryFn: () => api.get<ConnectedAccountListRead>(path) })
  const revoke = useMutation({
    mutationFn: (account: ConnectedAccountRead) =>
      api.delete<ConnectedAccountRead>(`${path}/${account.id}`, { 'If-Match': String(account.version) }),
    onSuccess: async () => {
      setError(null)
      await queryClient.invalidateQueries({ queryKey: key })
    },
    onError: (err) =>
      setError(err instanceof ApiError && err.status === 409 ? t('account.connected.conflict') : t('common.requestFailed')),
  })

  if (accounts.isError) return <Text c="dimmed" fz="sm">{t('common.requestFailed')}</Text>
  if (!accounts.data) return null
  return (
    <Stack gap="xs">
      {error && (
        <Alert color="red" role="alert">
          {error}
        </Alert>
      )}
      {accounts.data.items.length === 0 ? (
        <Text c="dimmed" fz="sm">
          {t('usersAdmin.fields.connectedAccountsNone')}
        </Text>
      ) : (
        <ConnectedAccountsTable
          accounts={accounts.data.items}
          revoking={revoke.isPending ? revoke.variables?.id : undefined}
          onRevoke={(account) => revoke.mutate(account)}
        />
      )}
    </Stack>
  )
}
