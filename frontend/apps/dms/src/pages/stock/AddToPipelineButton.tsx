import { Button } from '@mantine/core'
import { Workflow } from 'lucide-react'
import { useNavigate } from 'react-router-dom'
import { useTranslation } from 'react-i18next'
import { useOverlay } from '@nexotec/ui-kit'
import { api } from '../../api/client'
import { ConfiguratorOverlay } from '../../components/configurator/ConfiguratorOverlay'
import { HostCommitError } from '../../components/configurator/hostCommitError'
import { configurationLabel } from '../../components/configurator/configurationLabel'
import { useIdempotencyKey } from '../../hooks/useIdempotencyKey'
import type { ConfigurationRead, StockItemCreateRequest, StockItemRead } from '../../api/types'

/**
 * FR-C-13 — «Fahrzeug aufnehmen», the Stock list's one intake (KAN-463,
 * Anto 2026-10-26: the hand-typed form is gone; every stock vehicle enters
 * through the configurator, and the API refuses a car without its
 * configuration). The car lands in the pipeline; its purchase booking puts
 * it on stock with its VIN (ADR-103, KAN-446). The only
 * host where **both** modes are reachable: `build` for a factory order,
 * `record` for a car being bought in. Keeping `record` here is what makes
 * the offer workspace's build-only rule safe — a used car bought but not
 * yet received is this pipeline item, and the offer's Path A finds it.
 */
export function AddToPipelineButton() {
  const { t } = useTranslation()
  const overlay = useOverlay()
  const navigate = useNavigate()
  // KAN-266: one key per pipeline item. The configurator retries the host
  // commit when it failed (a response lost on the way back): the same
  // request keeps its key and the server replays the item it created, where
  // a key minted per request added the car twice. Renewed on every opening.
  const idempotency = useIdempotencyKey()

  const addToPipeline = async (configuration: ConfigurationRead) => {
    const body: StockItemCreateRequest = {
      vehicleLabel: configurationLabel(configuration),
      condition: configuration.mode === 'build' ? 'new' : 'used',
      configurationId: configuration.id,
      firstRegistrationDate: configuration.firstRegistrationDate,
      odometerKm: configuration.mileageKm,
    }
    let item: StockItemRead
    try {
      item = await api.post<StockItemRead>('/inventory/stock-items', body, idempotency.headers(body))
    } catch {
      throw new HostCommitError(t('stockList.addToPipelineError'))
    }
    idempotency.renew()
    overlay.pop()
    navigate(`/stock/${item.id}`)
  }

  const open = () => {
    idempotency.renew()
    overlay.push({
      key: 'configurator-add-to-pipeline',
      content: (
        <ConfiguratorOverlay
          allowedModes={['build', 'record']}
          onCommitted={addToPipeline}
          onClose={() => overlay.pop()}
        />
      ),
    })
  }

  return (
    <Button leftSection={<Workflow size={16} />} onClick={open}>
      {t('stockList.newStockItem')}
    </Button>
  )
}
