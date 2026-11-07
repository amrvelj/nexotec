import { useQueryClient } from '@tanstack/react-query'
import { useTranslation } from 'react-i18next'
import { useOverlay } from '@nexotec/ui-kit'
import { api } from '../../api/client'
import { ConfiguratorOverlay } from '../configurator/ConfiguratorOverlay'
import { HostCommitError } from '../configurator/hostCommitError'
import { useIdempotencyKey } from '../../hooks/useIdempotencyKey'
import { apiRefusalText } from '../../utils/apiRefusal'
import type { ConfigurationRead, StockItemConfigurationAttach, StockItemRead } from '../../api/types'

/** KAN-571: the button's flow as a hook, so the stock row menu's
 * «Konfiguration zuordnen» opens the very same configurator. */
export function useAttachConfiguration(item: StockItemRead): () => void {
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

  return () => {
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
}
