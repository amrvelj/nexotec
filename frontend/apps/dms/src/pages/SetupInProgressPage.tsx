import { Link } from 'react-router-dom'
import { Button, Center, Paper, Stack, Text, Title } from '@mantine/core'
import { useTranslation } from 'react-i18next'
import { useSetBreadcrumb } from '@nexotec/ui-kit'

/**
 * KAN-214 (D-A-21) — «Betrieb wird eingerichtet»: what a user who is not a
 * manager sees while the Betrieb is `pending_onboarding`. Presentation only:
 * the server answers 404 to every module route until activation. My account
 * stays reachable.
 */
export function SetupInProgressPage() {
  const { t } = useTranslation()
  useSetBreadcrumb([t('setup.inProgress.title')])
  return (
    <Center mih={360}>
      <Paper withBorder radius="md" p="xl" maw={560}>
        <Stack gap="sm" align="flex-start">
          <Title order={2}>{t('setup.inProgress.title')}</Title>
          <Text>{t('setup.inProgress.body')}</Text>
          <Button component={Link} to="/account/profile" variant="light">
            {t('setup.inProgress.account')}
          </Button>
        </Stack>
      </Paper>
    </Center>
  )
}
