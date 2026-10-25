import type { ReactNode } from 'react'
import { Alert, Button, Card, Group, Text } from '@mantine/core'
import { useTranslation } from 'react-i18next'

export interface SectionCardProps {
  title: string
  icon: ReactNode
  /** The section's `<h3>` id, so a test and a screen reader find the section by name. */
  children: ReactNode
  /** Omitted for a section that saves through its own dialogs (banking). */
  onSave?: () => void
  dirty?: boolean
  saving?: boolean
  saveDisabled?: boolean
  saveDisabledReason?: string | null
  error?: string | null
  onDismissError?: () => void
  saved?: boolean
}

/**
 * One section of the Business screen (KAN-203): a card with its own Save,
 * enabled once something changed, its refusal said in the UI language
 * beneath the fields, never a modal.
 */
export function SectionCard({
  title,
  icon,
  children,
  onSave,
  dirty = false,
  saving = false,
  saveDisabled = false,
  saveDisabledReason = null,
  error = null,
  onDismissError,
  saved = false,
}: SectionCardProps) {
  const { t } = useTranslation()
  return (
    <Card withBorder radius="md" padding="lg" component="section" aria-label={title}>
      <Group gap="sm" mb="md">
        {icon}
        <Text component="h3" fw={600} fz="lg" m={0}>
          {title}
        </Text>
      </Group>
      {children}
      {error && (
        <Alert color="red" role="alert" mt="md" withCloseButton closeButtonLabel={t('common.dismiss')} onClose={onDismissError}>
          {error}
        </Alert>
      )}
      {onSave && (
        <Group justify="flex-end" mt="md" gap="sm">
          {saved && !dirty && (
            <Text c="dimmed" fz="sm" role="status">
              {t('business.saved')}
            </Text>
          )}
          {saveDisabledReason && dirty && (
            <Text c="dimmed" fz="sm">
              {saveDisabledReason}
            </Text>
          )}
          <Button onClick={onSave} disabled={!dirty || saveDisabled} loading={saving}>
            {t('common.save')}
          </Button>
        </Group>
      )}
    </Card>
  )
}
