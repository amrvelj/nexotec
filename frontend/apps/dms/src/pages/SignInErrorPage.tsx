import { Center, Stack, Text } from '@mantine/core'
import { useTranslation } from 'react-i18next'
import { fontFamily, purple, slate, typography } from '@nexotec/ui-kit'
import { API_BASE_URL } from '../api/client'

/**
 * The one screen WP-4 builds. Zitadel hosts the actual login page — this
 * route only exists for the moment control returns to us with no valid
 * session (denied at Zitadel, account not provisioned in Nexotec, revoked,
 * wrong status). One generic sentence, never reason-specific — a
 * reason-specific message ("this account isn't set up yet") is a mild
 * account-enumeration leak for basically no user benefit.
 */
export function SignInErrorPage() {
  const { t } = useTranslation()

  return (
    <Center h="100vh" style={{ fontFamily }}>
      <Stack align="center" gap="md" maw={360}>
        <Text style={{ fontSize: typography.pageTitle.size, fontWeight: typography.pageTitle.weight, color: slate[9] }}>
          {t('signInError.title')}
        </Text>
        <Text ta="center" style={{ fontSize: typography.body.size, color: slate[6] }}>
          {t('signInError.message')}
        </Text>
        <a
          href={`${API_BASE_URL}/auth/oidc/login`}
          style={{ fontSize: typography.bodyStrong.size, fontWeight: typography.bodyStrong.weight, color: purple[6] }}
        >
          {t('signInError.retry')}
        </a>
      </Stack>
    </Center>
  )
}
