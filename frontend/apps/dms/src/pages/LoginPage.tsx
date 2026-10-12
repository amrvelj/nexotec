import { useEffect } from 'react'
import { Center, Loader, Stack, Text } from '@mantine/core'
import { useTranslation } from 'react-i18next'
import { API_BASE_URL } from '../api/client'

/**
 * This package builds no Nexotec login screen (WP-4) — Zitadel hosts the
 * actual sign-in page. This route exists only to trigger the full browser
 * navigation there; it's a full page load (window.location.href), never a
 * fetch/XHR — a same-origin API client call couldn't reach Zitadel's
 * hosted UI usefully, and the callback needs a real top-level redirect
 * round trip either way.
 */
export function LoginPage() {
  const { t } = useTranslation()
  useEffect(() => {
    window.location.href = `${API_BASE_URL}/auth/oidc/login`
  }, [])

  return (
    <Center h="100vh">
      <Stack align="center" gap="sm">
        <Loader />
        <Text c="dimmed">{t('login.redirecting')}</Text>
      </Stack>
    </Center>
  )
}
