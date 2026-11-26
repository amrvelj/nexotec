import { Alert, Button, Group, Text } from '@mantine/core'
import { useTranslation } from 'react-i18next'
import type { Refusal } from './refusal'

/** The refusal, with «Neu laden» only for a stale version. */
export function RefusalAlert({
  refusal,
  reloadLabel,
  onReload,
  onClose,
}: {
  refusal: Refusal | null
  reloadLabel: string
  onReload: () => void
  onClose?: () => void
}) {
  const { t } = useTranslation()
  if (!refusal) return null
  return (
    <Alert
      color="red"
      role="alert"
      withCloseButton={onClose !== undefined}
      closeButtonLabel={t('common.dismiss')}
      onClose={onClose}
    >
      <Group gap="sm" justify="space-between" wrap="nowrap">
        <Text fz="sm">{refusal.text}</Text>
        {refusal.stale && (
          <Button size="compact-xs" variant="light" onClick={onReload}>
            {reloadLabel}
          </Button>
        )}
      </Group>
    </Alert>
  )
}
