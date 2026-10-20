import { Button, Table, Text } from '@mantine/core'
import { Badge, type BadgeTone } from '@nexotec/ui-kit'
import { useTranslation } from 'react-i18next'
import type { ConnectedAccountManagerRead, ConnectedAccountRead } from '../../api/types'
import { formatDateTime } from '../../utils/format'

const STATUS_TONE: Record<ConnectedAccountRead['status'], BadgeTone> = {
  active: 'success',
  error: 'warning',
  revoked: 'slate',
}

type Props =
  | {
      /** The user's own grants (My account): account, permissions, status and stamps. */
      view: 'self'
      accounts: ConnectedAccountRead[]
      revoking?: string
      onRevoke: (account: ConnectedAccountRead) => void
    }
  | {
      /** A manager's read of a user's grants (D-A-15): provider, permissions and status only. */
      view: 'manager'
      accounts: ConnectedAccountManagerRead[]
      revoking?: string
      onRevoke: (account: ConnectedAccountManagerRead) => void
    }

/**
 * A user's connected accounts (KAN-201; FR-AU-24). The user sees their own
 * account's address and when it was linked and last used; the manager sees
 * provider, permissions and status only — never what an employee does with
 * their mailbox, never content.
 */
export function ConnectedAccountsTable(props: Props) {
  const { t } = useTranslation()
  const self = props.view === 'self'
  return (
    <Table verticalSpacing="sm">
      <Table.Thead>
        <Table.Tr>
          <Table.Th>{t('account.connected.columns.provider')}</Table.Th>
          {self && <Table.Th>{t('account.connected.columns.account')}</Table.Th>}
          <Table.Th>{t('account.connected.columns.scopes')}</Table.Th>
          <Table.Th>{t('account.connected.columns.status')}</Table.Th>
          {self && <Table.Th>{t('account.connected.columns.linkedAt')}</Table.Th>}
          {self && <Table.Th>{t('account.connected.columns.lastUsed')}</Table.Th>}
          <Table.Th />
        </Table.Tr>
      </Table.Thead>
      <Table.Tbody>
        {props.accounts.map((account) => (
          <Table.Tr key={account.id}>
            <Table.Td>{t(`account.connected.provider.${account.provider}`)}</Table.Td>
            {props.view === 'self' && 'linkedAt' in account && (
              <Table.Td>{account.externalAccountLabel ?? '—'}</Table.Td>
            )}
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
            {props.view === 'self' && 'linkedAt' in account && (
              <>
                <Table.Td>{formatDateTime(account.linkedAt)}</Table.Td>
                <Table.Td>
                  {account.lastUsedAt ? formatDateTime(account.lastUsedAt) : t('account.connected.neverUsed')}
                </Table.Td>
              </>
            )}
            <Table.Td>
              {account.status !== 'revoked' && (
                <Button
                  type="button"
                  size="xs"
                  variant="subtle"
                  color="red"
                  loading={props.revoking === account.id}
                  onClick={() => (props.view === 'self' ? props.onRevoke(account as ConnectedAccountRead) : props.onRevoke(account))}
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
