import { Button, Group, Modal, Stack, Text } from '@mantine/core'
import { useLocation, useNavigate } from 'react-router-dom'
import { useTranslation } from 'react-i18next'
import { useAuth } from '../../auth/AuthContext'
import { toSwissLocale, type SupportedLanguage } from '../../i18n'
import { formatDateOnly } from '../../utils/format'

/** Where the manager reads and accepts a new version (the lazy page's route). */
export const LEGAL_ACCEPTANCE_ROUTE = '/administration/legal-acceptance'

/**
 * KAN-214 (FR-A-39; Anto, 2026-11-13, Q1 A) — the prompt a manager of an
 * active Betrieb sees at every sign-in while a new ToS / DPA version is
 * within its grace. «Später» closes it for this session; past the grace
 * there is no prompt — `ProtectedRoute` shows the acceptance screen only.
 */
export function LegalAcceptancePrompt() {
  const { t, i18n } = useTranslation()
  const locale = toSwissLocale(i18n.language as SupportedLanguage)
  const { legalAcceptanceDue, legalPromptDismissed, dismissLegalPrompt } = useAuth()
  const navigate = useNavigate()
  const location = useLocation()

  const opened =
    legalAcceptanceDue !== null &&
    !legalAcceptanceDue.held &&
    !legalPromptDismissed &&
    location.pathname !== LEGAL_ACCEPTANCE_ROUTE

  return (
    <Modal
      opened={opened}
      onClose={dismissLegalPrompt}
      title={t('setup.acceptance.promptTitle')}
      closeButtonProps={{ 'aria-label': t('common.overlay.close') }}
      centered
    >
      <Stack gap="sm">
        <Text fz="sm">{t('setup.acceptance.intro')}</Text>
        <ul>
          {(legalAcceptanceDue?.documents ?? []).map((document) => (
            <li key={document.id}>
              <Text fz="sm">
                {`${t(`setup.documentTypes.${document.documentType}`)} · ${t('setup.legal.version', { version: document.version })}`}
              </Text>
            </li>
          ))}
        </ul>
        {legalAcceptanceDue?.dueBy && (
          <Text fz="sm" c="dimmed">
            {t('setup.acceptance.dueBy', { date: formatDateOnly(legalAcceptanceDue.dueBy, locale) })}
          </Text>
        )}
        <Group justify="flex-end">
          <Button variant="default" onClick={dismissLegalPrompt}>
            {t('setup.acceptance.later')}
          </Button>
          <Button
            onClick={() => {
              dismissLegalPrompt()
              navigate(LEGAL_ACCEPTANCE_ROUTE)
            }}
          >
            {t('setup.acceptance.readNow')}
          </Button>
        </Group>
      </Stack>
    </Modal>
  )
}
