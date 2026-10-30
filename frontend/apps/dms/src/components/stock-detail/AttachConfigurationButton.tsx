import { Button } from '@mantine/core'
import { useQueryClient } from '@tanstack/react-query'
import { Workflow } from 'lucide-react'
import { useTranslation } from 'react-i18next'
import { useOverlay } from '@nexotec/ui-kit'
import { api } from '../../api/client'
import { ConfiguratorOverlay } from '../configurator/ConfiguratorOverlay'
import { HostCommitError } from '../configurator/hostCommitError'
import { useIdempotencyKey } from '../../hooks/useIdempotencyKey'
import { apiRefusalText } from '../../utils/apiRefusal'
import type { ConfigurationRead, StockItemConfigurationAttach, StockItemRead } from '../../api/types'

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
  const queryClient = useQueryClient()
  const overlay = useOverlay()
  const idempotency = useIdempotencyKey()

  const attach = async (configuration: ConfigurationRead) => {
    const path = `/inventory/stock-items/${item.id}/configuration`
    const body: StockItemConfigurationAttach = { configurationId: configuration.id }
    let updated: StockItemRead
    try {
      updated = await api.post<StockItemRead>(path, body, {
        'If-Match': String(item.version),
        ...idempotency.headers([path, body]),
      })
    } catch (err) {
      throw new HostCommitError(apiRefusalText(t, err, 'stockDetail.configuration.attachError'))
    }
    idempotency.renew()
    overlay.pop()
    queryClient.setQueryData(['stock-item', item.id], updated)
  }

  const open = () => {
    idempotency.renew()
    overlay.push({
      key: 'configurator-attach-to-stock-item',
      content: (
        <ConfiguratorOverlay
          allowedModes={['build', 'record']}
          initialMode="record"
          initialVin={item.vin ?? ''}
          onCommitted={attach}
          onClose={() => overlay.pop()}
        />
      ),
    })
  }

  return (
    <Button variant="default" leftSection={<Workflow size={16} />} onClick={open}>
      {t('stockDetail.configuration.attach')}
    </Button>
  )
}
