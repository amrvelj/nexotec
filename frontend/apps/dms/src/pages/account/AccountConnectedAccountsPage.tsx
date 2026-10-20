import { useState } from 'react'
import { Alert, Card, Stack, Text } from '@mantine/core'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Link2 } from 'lucide-react'
import { useTranslation } from 'react-i18next'
import { useSetBreadcrumb } from '@nexotec/ui-kit'
import { api, ApiError } from '../../api/client'
import type { ConnectedAccountListRead, ConnectedAccountRead } from '../../api/types'
import { ConnectedAccountsTable } from '../../components/account/ConnectedAccountsTable'
import { CONNECTED_KEY } from './accountKeys'

/**
 * My account › Connected accounts (KAN-201; FR-AU-23/24, D-A-15): the
 * Microsoft 365 mailbox and calendar a user links to act on their behalf —
 * a grant, never a way to sign in. Linking ships with the Outlook
 * integration; until then the tab says what will appear here. The user may
 * revoke a grant; the row stays, revoked.
 */
export function AccountConnectedAccountsPage() {
  const { t } = useTranslation()
  useSetBreadcrumb([t('account.title'), t('account.tabs.connectedAccounts')])
  const queryClient = useQueryClient()
  const [error, setError] = useState<string | null>(null)
  const accounts = useQuery({
    queryKey: CONNECTED_KEY,
    queryFn: () => api.get<ConnectedAccountListRead>('/me/connected-accounts'),
  })
  const revoke = useMutation({
    mutationFn: (account: ConnectedAccountRead) =>
      api.delete<ConnectedAccountRead>(`/me/connected-accounts/${account.id}`, { 'If-Match': String(account.version) }),
    onSuccess: async () => {
      setError(null)
      await queryClient.invalidateQueries({ queryKey: CONNECTED_KEY })
    },
    onError: (err) =>
      setError(err instanceof ApiError && err.status === 409 ? t('account.connected.conflict') : t('common.requestFailed')),
  })

  if (accounts.isError) return <Alert color="red" role="alert">{t('common.requestFailed')}</Alert>
  if (!accounts.data) return null
  const items = accounts.data.items

  return (
    <Stack gap="lg">
      {error && (
        <Alert color="red" role="alert" withCloseButton closeButtonLabel={t('common.dismiss')} onClose={() => setError(null)}>
          {error}
        </Alert>
      )}
      <Card withBorder radius="md" padding="lg">
        {items.length === 0 ? (
          <Stack gap="xs" align="flex-start">
            <Link2 size={20} />
            <Text fw={600}>{t('account.connected.emptyTitle')}</Text>
            <Text c="dimmed" fz="sm">
              {t('account.connected.emptyBody')}
            </Text>
            <Text c="dimmed" fz="sm">
              {t('account.connected.privacy')}
            </Text>
          </Stack>
        ) : (
          <Stack gap="md">
            <Text c="dimmed" fz="sm">
              {t('account.connected.privacy')}
            </Text>
            <ConnectedAccountsTable
              view="self"
              accounts={items}
              revoking={revoke.isPending ? revoke.variables?.id : undefined}
              onRevoke={(account) => revoke.mutate(account)}
            />
          </Stack>
        )}
      </Card>
    </Stack>
  )
}
