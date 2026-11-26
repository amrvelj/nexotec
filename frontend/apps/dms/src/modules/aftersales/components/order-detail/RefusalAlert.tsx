import { Alert, Button, Stack } from '@mantine/core'
import { useTranslation } from 'react-i18next'
import { apiRefusalText, isStaleVersion } from '../../../../utils/apiRefusal'

/**
 * A refused change of the order in one sentence, in the UI language
 * (`apiRefusalText`); a stale If-Match 409 — someone changed the order since
 * it was read — offers «Neu laden».
 */
export function RefusalAlert({
  error,
  fallbackKey,
  onReload,
  onDismiss,
}: {
  error: unknown
  fallbackKey: string
  onReload: () => void
  onDismiss?: () => void
}) {
  const { t } = useTranslation()
  return (
    <Alert
      color="red"
      role="alert"
      withCloseButton={Boolean(onDismiss)}
      closeButtonLabel={t('common.dismiss')}
      onClose={onDismiss}
    >
      <Stack gap="xs" align="flex-start">
        <span>{apiRefusalText(t, error, fallbackKey)}</span>
        {isStaleVersion(error) && (
          <Button size="xs" variant="default" onClick={onReload}>
            {t('serviceOrder.reload')}
          </Button>
        )}
      </Stack>
    </Alert>
  )
}
