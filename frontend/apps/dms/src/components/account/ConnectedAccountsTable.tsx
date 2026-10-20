import { Button, Table, Text } from '@mantine/core'
import { Badge, type BadgeTone } from '@nexotec/ui-kit'
import { useTranslation } from 'react-i18next'
import type { ConnectedAccountRead } from '../../api/types'
import { formatDateTime } from '../../utils/format'

const STATUS_TONE: Record<ConnectedAccountRead['status'], BadgeTone> = {
  active: 'success',
  error: 'warning',
  revoked: 'slate',
}

/**
 * A user's connected accounts (KAN-201; FR-AU-24): provider, account,
 * permissions, status, linked and last-used — never content, which nothing
 * in Nexotec reads for display. The same table on My account and on the
 * Users screen (the manager's read and revoke).
 */
export function ConnectedAccountsTable({
  accounts,
  revoking,
  onRevoke,
}: {
  accounts: ConnectedAccountRead[]
  revoking?: string
  onRevoke: (account: ConnectedAccountRead) => void
}) {
  const { t } = useTranslation()
  return (
    <Table verticalSpacing="sm">
      <Table.Thead>
        <Table.Tr>
          <Table.Th>{t('account.connected.columns.provider')}</Table.Th>
          <Table.Th>{t('account.connected.columns.account')}</Table.Th>
          <Table.Th>{t('account.connected.columns.scopes')}</Table.Th>
          <Table.Th>{t('account.connected.columns.status')}</Table.Th>
          <Table.Th>{t('account.connected.columns.linkedAt')}</Table.Th>
          <Table.Th>{t('account.connected.columns.lastUsed')}</Table.Th>
          <Table.Th />
        </Table.Tr>
      </Table.Thead>
      <Table.Tbody>
        {accounts.map((account) => (
          <Table.Tr key={account.id}>
            <Table.Td>{t(`account.connected.provider.${account.provider}`)}</Table.Td>
            <Table.Td>{account.externalAccountLabel ?? '—'}</Table.Td>
            <Table.Td>
              <Text fz="xs" c="dimmed">
                {account.scopes.join(', ')}
              </Text>
            </Table.Td>
            <Table.Td>
              <Badge tone={STATUS_TONE[account.status]} dot>
                {t(`account.connected.status.${account.status}`)}
              </Badge>
            </Table.Td>
            <Table.Td>{formatDateTime(account.linkedAt)}</Table.Td>
            <Table.Td>{account.lastUsedAt ? formatDateTime(account.lastUsedAt) : t('account.connected.neverUsed')}</Table.Td>
            <Table.Td>
              {account.status !== 'revoked' && (
                <Button
                  type="button"
                  size="xs"
                  variant="subtle"
                  color="red"
                  loading={revoking === account.id}
                  onClick={() => onRevoke(account)}
                >
                  {t('account.connected.revoke')}
                </Button>
              )}
            </Table.Td>
          </Table.Tr>
        ))}
      </Table.Tbody>
    </Table>
  )
}
