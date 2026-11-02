import type { ReactNode } from 'react'
import { Link } from 'react-router-dom'
import { Anchor, Center, Group, Paper, Stack, Text, Title } from '@mantine/core'
import { useTranslation } from 'react-i18next'
import { LEGAL_PRIVACY_PATH, LEGAL_TERMS_PATH } from '../../config/legalPages'

/** The layout every unauthenticated identity screen shares (KAN-196): one
 * card, centred, the product name above it. Layout only — the components
 * inside are Mantine's, as everywhere else.
 *
 * KAN-452: below the card, the public Terms of Use and Privacy Policy — on
 * the sign-in screen and every other identity screen alike. */
export function AuthScreen({ title, hint, children }: { title: string; hint?: string; children: ReactNode }) {
  const { t } = useTranslation()
  return (
    <Center mih="100vh" p="md">
      <Stack w="100%" maw={420} gap="md">
        <Text ta="center" fw={600} fz="lg">
          Nexotec
        </Text>
        <Paper withBorder radius="md" p="xl" shadow="xs">
          <Stack gap="md">
            <div>
              <Title order={2} fz="h3">
                {title}
              </Title>
              {hint && (
                <Text c="dimmed" fz="sm" mt={4}>
                  {hint}
                </Text>
              )}
            </div>
            {children}
          </Stack>
        </Paper>
        <Group component="nav" aria-label={t('legal.linksLabel')} justify="center" gap="md">
          <Anchor component={Link} to={LEGAL_TERMS_PATH} fz="xs" c="dimmed">
            {t('legal.termsLink')}
          </Anchor>
          <Anchor component={Link} to={LEGAL_PRIVACY_PATH} fz="xs" c="dimmed">
            {t('legal.privacyLink')}
          </Anchor>
        </Group>
      </Stack>
    </Center>
  )
}
