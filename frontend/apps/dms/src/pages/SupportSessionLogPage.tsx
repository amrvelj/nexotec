import { Stack, Text, Title } from '@mantine/core'
import { useTranslation } from 'react-i18next'
import { useSetBreadcrumb } from '@nexotec/ui-kit'
import { useAuth } from '../auth/AuthContext'
import { SupportSessionLogGrid } from '../components/supportSessions/SupportSessionLogGrid'

/**
 * KAN-221 PR-6 — Dealer Administration › Support sessions (FR-A-15; the
 * PRD's matrix row «Support-session log»): every session Nexotec staff held
 * in the active Betrieb, running or over, newest first. The manager reads it
 * (`support_session_log`); anyone else is told so — the entry is never
 * role-hidden (A-RULE-7).
 */
export function SupportSessionLogPage() {
  const { t } = useTranslation()
  useSetBreadcrumb([t('shell.nav.administration'), t('supportSessions.log.title')])
  const { activeDealership } = useAuth()
  const dealershipId = activeDealership?.id ?? ''
  return (
    <Stack gap="md">
      <Title order={2}>{t('supportSessions.log.title')}</Title>
      <Text size="sm" c="dimmed">
        {t('supportSessions.log.intro')}
      </Text>
      {dealershipId && (
        <SupportSessionLogGrid gridKey="supportSessionLog" path={`/dealerships/${dealershipId}/support-sessions`} />
      )}
    </Stack>
  )
}
