import { useEffect, useRef, useState } from 'react'
import { Alert, Anchor, Group, Loader, Stack, Text, Title } from '@mantine/core'
import { useQueryClient } from '@tanstack/react-query'
import { useTranslation } from 'react-i18next'
import { Link, useLocation, useNavigate } from 'react-router-dom'
import { api } from '../api/client'
import type { LinkOutcomeRead } from '../api/types'
import { apiRefusalText } from '../utils/apiRefusal'
import { callbackBody, mailNavigation } from './mail/mailLink'

/**
 * KAN-215 — `/mail-link/callback`: where Microsoft or Google sends the browser
 * back after the sign-in (and after Microsoft's admin consent). The query is
 * posted as it came; the server decides everything from the state it issued
 * (the provider tenant is never taken from here). Then: on to the consent
 * screen, or back to the screen the link started from — My account › Mein
 * Postfach for one's own mailbox, Administration › Mails an Kontakte for the
 * Betrieb's. A refusal is said in the UI language with the way back.
 */
export function MailLinkCallbackPage() {
  const { t } = useTranslation()
  const location = useLocation()
  const navigate = useNavigate()
  const queryClient = useQueryClient()
  const [phase, setPhase] = useState<'working' | 'consent' | 'failed'>('working')
  const [error, setError] = useState<string | null>(null)
  const sent = useRef(false)

  useEffect(() => {
    if (sent.current) return // the state is single-use: post it once, even under StrictMode
    sent.current = true
    const body = callbackBody(location.search)
    if (!body) {
      setPhase('failed')
      setError(t('mail.refusals.link_expired'))
      return
    }
    api
      .post<LinkOutcomeRead>('/integrations/mail/link-callback', body)
      .then(async (outcome) => {
        if (outcome.status === 'consent_required' && outcome.redirectUrl) {
          setPhase('consent')
          mailNavigation.toProvider(outcome.redirectUrl)
          return
        }
        await queryClient.invalidateQueries({ queryKey: ['mail'] })
        navigate(outcome.kind === 'business_mailbox' ? '/administration/mail' : '/account/mailbox', { replace: true })
      })
      .catch((err: unknown) => {
        setPhase('failed')
        setError(apiRefusalText(t, err, 'common.requestFailed'))
      })
  }, [location.search, navigate, queryClient, t])

  if (phase === 'failed') {
    return (
      <Stack gap="md" p="md" maw={640}>
        <Title order={2}>{t('mail.callback.failedTitle')}</Title>
        <Alert color="red" variant="light" role="alert">
          {error}
        </Alert>
        <Group gap="md">
          <Anchor component={Link} to="/account/mailbox">
            {t('mail.links.ownScreen')}
          </Anchor>
          <Anchor component={Link} to="/administration/mail">
            {t('mail.links.businessScreen')}
          </Anchor>
        </Group>
      </Stack>
    )
  }
  return (
    <Group gap="sm" p="md">
      <Loader size="sm" />
      <Text>{phase === 'consent' ? t('mail.callback.consent') : t('mail.callback.working')}</Text>
    </Group>
  )
}
