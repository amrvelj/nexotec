import { useEffect, useRef, useState } from 'react'
import { Alert, Anchor, Group, Loader, Stack, Text, Title } from '@mantine/core'
import { useQueryClient } from '@tanstack/react-query'
import { useTranslation } from 'react-i18next'
import { Link, useLocation, useNavigate } from 'react-router-dom'
import { api } from '../api/client'
import type { BusinessProfileLinkOutcomeRead } from '../api/types'
import { apiRefusalText } from '../utils/apiRefusal'
import { businessProfileCallbackBody } from './businessProfile/businessProfileLink'

const INTEGRATIONS = '/administration/integrations'

/**
 * KAN-226 PR-4 — `/business-profile-link/callback`: where Google sends the
 * browser back after the manager signed in for one location. The query is
 * posted as it came (the state is single-use: once, even under StrictMode);
 * the server decides everything from the state it issued. Then back to
 * Administration › Integrationen, where the location's card asks for the
 * Google location to bind. A refusal is said in the UI language with the way
 * back.
 */
export function BusinessProfileLinkCallbackPage() {
  const { t } = useTranslation()
  const location = useLocation()
  const navigate = useNavigate()
  const queryClient = useQueryClient()
  const [error, setError] = useState<string | null>(null)
  const sent = useRef(false)

  useEffect(() => {
    if (sent.current) return
    sent.current = true
    const body = businessProfileCallbackBody(location.search)
    if (!body) {
      setError(t('businessProfile.card.callback.expired'))
      return
    }
    api
      .post<BusinessProfileLinkOutcomeRead>('/integrations/business-profile/link-callback', body)
      .then(async () => {
        await queryClient.invalidateQueries({ queryKey: ['business-profile'] })
        navigate(INTEGRATIONS, { replace: true })
      })
      .catch((err: unknown) => setError(apiRefusalText(t, err, 'common.requestFailed')))
  }, [location.search, navigate, queryClient, t])

  if (error) {
    return (
      <Stack gap="md" p="md" maw={640}>
        <Title order={2}>{t('businessProfile.card.callback.failedTitle')}</Title>
        <Alert color="red" variant="light" role="alert">
          {error}
        </Alert>
        <Anchor component={Link} to={INTEGRATIONS}>
          {t('businessProfile.card.callback.back')}
        </Anchor>
      </Stack>
    )
  }
  return (
    <Group gap="sm" p="md">
      <Loader size="sm" />
      <Text>{t('businessProfile.card.callback.working')}</Text>
    </Group>
  )
}
