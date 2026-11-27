import { Alert, Button, Stack } from '@mantine/core'
import { useTranslation } from 'react-i18next'
import { ApiError } from '../../../../api/client'
import { apiRefusalText, isStaleVersion } from '../../../../utils/apiRefusal'

/** Ruling 49, check 6 (KAN-811): somebody is clocked on the accepted job — the change is asked first. */
export function asksClockedConfirm(error: unknown): boolean {
  return error instanceof ApiError && error.details?.reason === 'service_order_job_clocked_confirm'
}

/**
 * A refused change of the order in one sentence, in the UI language
 * (`apiRefusalText`); a stale If-Match 409 — someone changed the order since
 * it was read — offers «Neu laden». A change asked first because somebody is
 * clocked on the job (check 6) offers «Trotzdem ändern» when the caller can
 * send it again confirmed.
 */
export function RefusalAlert({
  error,
  fallbackKey,
  onReload,
  onDismiss,
  onConfirmClocked,
}: {
  error: unknown
  fallbackKey: string
  onReload: () => void
  onDismiss?: () => void
  onConfirmClocked?: () => void
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
        {onConfirmClocked && asksClockedConfirm(error) && (
          <Button size="xs" color="orange" onClick={onConfirmClocked}>
            {t('serviceOrder.answer.changeAnyway')}
          </Button>
        )}
        {isStaleVersion(error) && (
          <Button size="xs" variant="default" onClick={onReload}>
            {t('serviceOrder.reload')}
          </Button>
        )}
      </Stack>
    </Alert>
  )
}
