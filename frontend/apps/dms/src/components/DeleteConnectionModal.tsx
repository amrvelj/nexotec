import { useEffect, useState } from 'react'
import { Button, Group, Modal, Stack, Text } from '@mantine/core'
import { useTranslation } from 'react-i18next'
import { api } from '../api/client'
import type { IntegrationConnectionRead } from '../api/types'
import { apiRefusalText } from '../utils/apiRefusal'

/**
 * The account-identifier of a connection, as its card and the delete
 * confirmation name it. auto-i-dat (KAN-488, ADR-111) is an OAuth 2.0 client:
 * its `clientId` is the identifier (never the secret, which `config` never
 * carries). Every other provider falls back to its plain `username`.
 */
export function connectionAccountIdentifier(connection: IntegrationConnectionRead): string {
  const { clientId, username } = connection.config
  if (typeof clientId === 'string' && clientId !== '') return clientId
  return typeof username === 'string' && username !== '' ? username : '—'
}

/**
 * KAN-603 — «Verbindung löschen» from the card (dealer) or the grid row
 * (platform). The confirmation names provider, environment and account and
 * says the stored secrets go too; the request is the endpoint's own contract
 * (KAN-396, KAN-429): `If-Match` with the version the screen shows and
 * `{ confirm: true }`. A refusal stays in the dialog, in the UI language.
 */
export function DeleteConnectionModal({
  connection,
  providerName,
  onClose,
  onDeleted,
}: {
  connection: IntegrationConnectionRead | null
  providerName: string
  onClose: () => void
  onDeleted: () => void
}) {
  const { t } = useTranslation()
  const [submitting, setSubmitting] = useState(false)
  const [error, setError] = useState<string | null>(null)
  useEffect(() => setError(null), [connection?.id])

  if (!connection) return null

  const remove = async () => {
    setSubmitting(true)
    setError(null)
    try {
      await api.delete(
        `/integrations/connections/${connection.id}`,
        { 'If-Match': String(connection.version) },
        { confirm: true }
      )
      onDeleted()
      onClose()
    } catch (err) {
      setError(apiRefusalText(t, err, 'integrationsList.deleteModal.error'))
    } finally {
      setSubmitting(false)
    }
  }

  return (
    <Modal opened onClose={onClose} title={t('integrationsList.deleteModal.title', { name: connection.displayName })}>
      <Stack gap="sm">
        {error && <Text size="sm" c="red" role="alert">{error}</Text>}
        <Text size="sm">
          {t('integrationsList.deleteModal.body', {
            provider: providerName,
            environment: t(`integrationEnums.environment.${connection.environment}`, connection.environment),
            account: connectionAccountIdentifier(connection),
          })}
        </Text>
        <Text size="sm" fw={600}>{t('integrationsList.deleteModal.warning')}</Text>
        <Group justify="flex-end">
          <Button variant="default" onClick={onClose}>{t('common.cancel')}</Button>
          <Button color="red" onClick={() => void remove()} loading={submitting}>
            {t('integrationsList.deleteModal.confirm')}
          </Button>
        </Group>
      </Stack>
    </Modal>
  )
}
