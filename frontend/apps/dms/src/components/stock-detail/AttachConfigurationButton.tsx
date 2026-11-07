import { Button } from '@mantine/core'
import { Workflow } from 'lucide-react'
import { useTranslation } from 'react-i18next'
import type { StockItemRead } from '../../api/types'
import { useAttachConfiguration } from './useAttachConfiguration'

interface AttachConfigurationButtonProps {
  item: StockItemRead
}

/**
 * KAN-463 — «Konfiguration zuordnen» for a stock item without a
 * configuration (one the retired hand-typed intake created). The same
 * configurator the Stock list's intake opens, with both modes and `record`
 * first (a car already on the lot was bought in); its VIN prefills the
 * identification. On commit the configuration is linked through
 * `POST …/stock-items/{id}/configuration` (If-Match, audited), and the
 * stock detail's query takes the answer, so the technical data appear. One
 * Idempotency-Key per opening, as the intake's (KAN-266): a retried commit
 * replays the link instead of a version conflict.
 */
export function AttachConfigurationButton({ item }: AttachConfigurationButtonProps) {
  const { t } = useTranslation()
  const open = useAttachConfiguration(item)

  return (
    <Button variant="default" leftSection={<Workflow size={16} />} onClick={open}>
      {t('stockDetail.configuration.attach')}
    </Button>
  )
}
