import { useState } from 'react'
import { Alert, Badge, Button, Card, Group, Select, Skeleton, Stack, Text, TextInput, Title } from '@mantine/core'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useTranslation } from 'react-i18next'
import { KeyValueRow, slate, useSetBreadcrumb } from '@nexotec/ui-kit'
import { api } from '../api/client'
import type { BusinessMailboxRead, LinkStartRead, MailboxRead, MailProviderCode } from '../api/types'
import { useAuth } from '../auth/AuthContext'
import { useCanWrite } from '../auth/useCanWrite'
import { useIdempotencyKey } from '../hooks/useIdempotencyKey'
import { apiRefusalText } from '../utils/apiRefusal'
import { businessMailboxKey, MAIL_PROVIDERS, mailNavigation } from './mail/mailLink'

const STATUS_COLOR: Record<MailboxRead['status'], string> = { active: 'green', error: 'red', disconnected: 'gray' }

/**
 * KAN-215 — Dealer Administration › Kundenmails (`/administration/mail`; D-A-28
 * as re-cut by Anto on 2026-11-14, A-RULE-0: the one place the Betrieb sets up
 * its customer mail). Customer mail leaves only through a mailbox of the
 * Betrieb: a person's own (My account › Mein Postfach) or, failing that, the
 * Betrieb's shared mailbox connected here by a manager, who then signs in at
 * Microsoft or Google as an administrator of the organisation (Anto's Q2 B:
 * admin consent with tenant-wide send). The screen says in the dealer's words
 * what that consent allows and how their IT narrows it. Finance and the
 * manager read; the manager connects and disconnects (never role-hidden,
 * A-RULE-7). A colleague's own mailbox is never listed here (D-A-15).
 */
export function MailAdministrationPage() {
  const { t } = useTranslation()
  useSetBreadcrumb([t('shell.nav.administration'), t('mail.title')])
  const { activeDealership } = useAuth()
  const dealershipId = activeDealership?.id ?? ''
  const canWrite = useCanWrite('sending_identities')
  const mailbox = useQuery({
    queryKey: businessMailboxKey(dealershipId),
    queryFn: () => api.get<BusinessMailboxRead>(`/dealerships/${dealershipId}/mail/business-mailbox`),
    enabled: dealershipId !== '',
  })

  return (
    <Stack gap="lg">
      <Stack gap={2}>
        <Title order={2}>{t('mail.title')}</Title>
        <Text c={slate[5]} size="sm">
          {t('mail.subtitle')}
        </Text>
      </Stack>
      {mailbox.isError && (
        <Alert color="red" variant="light" role="alert">
          {apiRefusalText(t, mailbox.error, 'mail.loadError')}
        </Alert>
      )}
      {mailbox.isLoading && <Skeleton height={160} />}
      {mailbox.data && <BusinessMailboxCard dealershipId={dealershipId} data={mailbox.data} canWrite={canWrite} />}
      <ConsentExplanation />
    </Stack>
  )
}

function BusinessMailboxCard({
  dealershipId,
  data,
  canWrite,
}: {
  dealershipId: string
  data: BusinessMailboxRead
  canWrite: boolean
}) {
  const { t } = useTranslation()
  const queryClient = useQueryClient()
  const idempotency = useIdempotencyKey()
  const configured = data.providers.filter((provider) => provider.configured).map((provider) => provider.code)
  const [provider, setProvider] = useState<MailProviderCode | null>(configured[0] ?? null)
  const [address, setAddress] = useState('')
  const [confirming, setConfirming] = useState(false)
  const [error, setError] = useState<string | null>(null)

  const connect = useMutation({
    mutationFn: (body: { provider: MailProviderCode; mailboxAddress: string }) => {
      const path = `/dealerships/${dealershipId}/mail/business-mailbox/links`
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
      api.delete<void>(`/dealerships/${dealershipId}/mail/business-mailbox/${mailbox.id}`, {
        'If-Match': String(mailbox.version),
      }),
    onSuccess: async () => {
      setConfirming(false)
      setError(null)
      await queryClient.invalidateQueries({ queryKey: businessMailboxKey(dealershipId) })
    },
    onError: (err) => {
      setError(apiRefusalText(t, err, 'common.requestFailed'))
      void queryClient.invalidateQueries({ queryKey: businessMailboxKey(dealershipId) })
    },
  })

  const mailbox = data.mailbox
  return (
    <Card withBorder padding="lg" radius="md">
      <Stack gap="md">
        <Text fw={600}>{t('mail.business.heading')}</Text>
        {error && (
          <Alert color="red" variant="light" role="alert" withCloseButton closeButtonLabel={t('common.dismiss')} onClose={() => setError(null)}>
            {error}
          </Alert>
        )}
        {mailbox ? (
          <Stack gap="xs">
            <KeyValueRow label={t('mail.business.addressLabel')}>
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
            </KeyValueRow>
            {mailbox.errorCode && (
              <Text size="sm" c="red">
                {t('mail.refusals.business_mailbox_in_error')}
              </Text>
            )}
            {canWrite &&
              (confirming ? (
                <Stack gap="xs">
                  <Text size="sm" c={slate[6]}>
                    {t('mail.business.disconnectNote')}
                  </Text>
                  <Group gap="xs">
                    <Button color="red" size="xs" loading={disconnect.isPending} onClick={() => disconnect.mutate(mailbox)}>
                      {t('mail.business.confirmDisconnect')}
                    </Button>
                    <Button size="xs" variant="default" onClick={() => setConfirming(false)}>
                      {t('common.cancel')}
                    </Button>
                  </Group>
                </Stack>
              ) : (
                <Group>
                  <Button size="xs" variant="default" onClick={() => setConfirming(true)}>
                    {t('mail.business.disconnect')}
                  </Button>
                </Group>
              ))}
          </Stack>
        ) : (
          <Text size="sm" c={slate[6]}>
            {t('mail.business.none')}
          </Text>
        )}
        {!mailbox && canWrite && (
          <form
            onSubmit={(event) => {
              event.preventDefault()
              if (provider && address.trim()) connect.mutate({ provider, mailboxAddress: address.trim() })
            }}
          >
            <Stack gap="sm">
              <Text fw={500} size="sm">
                {t('mail.business.connectTitle')}
              </Text>
              <Select
                label={t('mail.business.providerLabel')}
                data={MAIL_PROVIDERS.map((code) => ({
                  value: code,
                  label: configured.includes(code)
                    ? t(`mail.providers.${code}`)
                    : `${t(`mail.providers.${code}`)} — ${t('mail.business.providerNotConfigured')}`,
                  disabled: !configured.includes(code),
                }))}
                value={provider}
                onChange={(value) => setProvider(value as MailProviderCode | null)}
                allowDeselect={false}
              />
              <TextInput
                type="email"
                required
                label={t('mail.business.addressLabel')}
                placeholder={t('mail.business.addressPlaceholder')}
                value={address}
                onChange={(event) => setAddress(event.currentTarget.value)}
              />
              <Text size="xs" c={slate[5]}>
                {t('mail.business.connectHint')}
              </Text>
              <Group>
                <Button type="submit" loading={connect.isPending} disabled={!provider || !address.trim()}>
                  {t('mail.business.connect')}
                </Button>
              </Group>
            </Stack>
          </form>
        )}
        {!canWrite && (
          <Text size="xs" c={slate[5]}>
            {t('mail.business.readOnly')}
          </Text>
        )}
      </Stack>
    </Card>
  )
}

function ConsentExplanation() {
  const { t } = useTranslation()
  return (
    <Card withBorder padding="lg" radius="md">
      <Stack gap="md">
        <Text fw={600}>{t('mail.business.grantedTitle')}</Text>
        {MAIL_PROVIDERS.map((code) => (
          <Stack key={code} gap={4}>
            <Text size="sm" fw={500}>
              {t(`mail.providers.${code}`)}
            </Text>
            <Text size="sm">{t(`mail.business.granted.${code}`)}</Text>
            <Text size="sm" fw={500} c={slate[6]}>
              {t('mail.business.restrictTitle')}
            </Text>
            <Text size="sm" c={slate[6]}>
              {t(`mail.business.restrict.${code}`)}
            </Text>
          </Stack>
        ))}
      </Stack>
    </Card>
  )
}
