import { useState } from 'react'
import { Alert, Anchor, Badge, Button, Card, Group, Stack, Text } from '@mantine/core'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Mail } from 'lucide-react'
import { useTranslation } from 'react-i18next'
import { Link } from 'react-router-dom'
import { slate, useSetBreadcrumb } from '@nexotec/ui-kit'
import { api } from '../../api/client'
import type { LinkStartRead, MailboxRead, MailProviderCode, OwnMailboxRead } from '../../api/types'
import { useIdempotencyKey } from '../../hooks/useIdempotencyKey'
import { apiRefusalText } from '../../utils/apiRefusal'
import { MAIL_PROVIDERS, mailNavigation, OWN_MAILBOX_KEY } from '../mail/mailLink'

const STATUS_COLOR: Record<MailboxRead['status'], string> = { active: 'green', error: 'red', disconnected: 'gray' }

/**
 * KAN-215 — My account › Mein Postfach (`/account/mailbox`): the user's own
 * Microsoft 365 or Google Workspace mailbox, connected by their own sign-in,
 * so contacts get their mail from them (Anto's D1 A: the author's own
 * mailbox first). It says which address contact mail goes from right now —
 * the own mailbox, the Betrieb's shared one, or why nothing can be sent, with
 * the way to fix it (no silent fallback). Every user, for themselves only.
 */
export function AccountMailboxPage() {
  const { t } = useTranslation()
  useSetBreadcrumb([t('account.title'), t('account.tabs.mailbox')])
  const queryClient = useQueryClient()
  const idempotency = useIdempotencyKey()
  const [error, setError] = useState<string | null>(null)
  const [confirming, setConfirming] = useState(false)
  const own = useQuery({ queryKey: OWN_MAILBOX_KEY, queryFn: () => api.get<OwnMailboxRead>('/integrations/mail/own-mailbox') })
  const connect = useMutation({
    mutationFn: (provider: MailProviderCode) => {
      const path = '/integrations/mail/own-mailbox/links'
      const body = { provider }
      return api.post<LinkStartRead>(path, body, idempotency.headers({ path, body }))
    },
    onSuccess: (started) => {
      idempotency.renew()
      mailNavigation.toProvider(started.authorizeUrl)
    },
    onError: (err) => setError(apiRefusalText(t, err, 'common.requestFailed')),
  })
  const disconnect = useMutation({
    mutationFn: (mailbox: MailboxRead) =>
      api.delete<void>(`/integrations/mail/own-mailbox/${mailbox.id}`, { 'If-Match': String(mailbox.version) }),
    onSuccess: async () => {
      setConfirming(false)
      setError(null)
      await queryClient.invalidateQueries({ queryKey: OWN_MAILBOX_KEY })
    },
    onError: (err) => {
      setError(apiRefusalText(t, err, 'common.requestFailed'))
      void queryClient.invalidateQueries({ queryKey: OWN_MAILBOX_KEY })
    },
  })

  if (own.isError) return <Alert color="red" role="alert">{apiRefusalText(t, own.error, 'mail.loadError')}</Alert>
  if (!own.data) return null
  const { mailbox, sender, senderRefusal, providers } = own.data
  const configured = new Set(providers.filter((provider) => provider.configured).map((provider) => provider.code))

  return (
    <Stack gap="lg">
      {error && (
        <Alert color="red" role="alert" withCloseButton closeButtonLabel={t('common.dismiss')} onClose={() => setError(null)}>
          {error}
        </Alert>
      )}
      <Card withBorder radius="md" padding="lg">
        <Stack gap="md">
          <Group gap="xs">
            <Mail size={20} />
            <Text fw={600}>{t('mail.own.heading')}</Text>
          </Group>
          <Text c="dimmed" fz="sm">
            {t('mail.own.intro')}
          </Text>
          {mailbox ? (
            <Stack gap="xs">
              <Group gap="xs">
                <Text size="sm">
                  {t('mail.business.connectedAs', {
                    address: mailbox.mailboxAddress,
                    provider: t(`mail.providers.${mailbox.provider}`),
                  })}
                </Text>
                <Badge color={STATUS_COLOR[mailbox.status]} variant="light">
                  {t(`mail.status.${mailbox.status}`)}
                </Badge>
              </Group>
              {confirming ? (
                <Group gap="xs">
                  <Button color="red" size="xs" loading={disconnect.isPending} onClick={() => disconnect.mutate(mailbox)}>
                    {t('mail.own.confirmDisconnect')}
                  </Button>
                  <Button size="xs" variant="default" onClick={() => setConfirming(false)}>
                    {t('common.cancel')}
                  </Button>
                </Group>
              ) : (
                <Group>
                  <Button size="xs" variant="default" onClick={() => setConfirming(true)}>
                    {t('mail.own.disconnect')}
                  </Button>
                </Group>
              )}
            </Stack>
          ) : (
            <Stack gap="xs">
              <Text size="sm" c={slate[6]}>
                {t('mail.own.none')}
              </Text>
              <Group gap="xs">
                {MAIL_PROVIDERS.map((code) => (
                  <Button
                    key={code}
                    variant="default"
                    disabled={!configured.has(code)}
                    loading={connect.isPending && connect.variables === code}
                    onClick={() => connect.mutate(code)}
                    title={configured.has(code) ? undefined : t('mail.business.providerNotConfigured')}
                  >
                    {t('mail.own.connect', { provider: t(`mail.providers.${code}`) })}
                  </Button>
                ))}
              </Group>
            </Stack>
          )}
          <Text c="dimmed" fz="xs">
            {t('mail.own.privacy')}
          </Text>
        </Stack>
      </Card>
      <Card withBorder radius="md" padding="lg">
        <Stack gap="xs">
          <Text fw={600}>{t('mail.own.senderTitle')}</Text>
          {sender ? (
            <Text size="sm">{t(`mail.own.sender.${sender.kind}`, { address: sender.address })}</Text>
          ) : (
            <Alert color="yellow" variant="light" role="status">
              <Stack gap={4}>
                <Text size="sm">{t(`mail.refusals.${senderRefusal ?? 'no_mailbox_connected'}`)}</Text>
                <Group gap="md">
                  <Anchor component={Link} to="/account/mailbox" fz="sm">
                    {t('mail.links.ownScreen')}
                  </Anchor>
                  <Anchor component={Link} to="/administration/mail" fz="sm">
                    {t('mail.links.businessScreen')}
                  </Anchor>
                </Group>
              </Stack>
            </Alert>
          )}
        </Stack>
      </Card>
    </Stack>
  )
}
