import { Button } from '@mantine/core'
import { Workflow } from 'lucide-react'
import { useNavigate } from 'react-router-dom'
import { useTranslation } from 'react-i18next'
import { useOverlay } from '@nexotec/ui-kit'
import { api } from '../../api/client'
import { ConfiguratorOverlay } from '../../components/configurator/ConfiguratorOverlay'
import { HostCommitError } from '../../components/configurator/hostCommitError'
import { configurationLabel } from '../../components/configurator/configurationLabel'
import type { ConfigurationRead, StockItemCreate, StockItemRead } from '../../api/types'

/**
 * FR-C-13 — "add a vehicle to the pipeline" from the Stock list. The only
 * host where **both** modes are reachable: `build` for a factory order,
 * `record` for a car being bought in. Keeping `record` here is what makes
 * the offer workspace's build-only rule safe — a used car bought but not
 * yet received is this pipeline item, and the offer's Path A finds it.
 */
export function AddToPipelineButton() {
  const { t } = useTranslation()
  const overlay = useOverlay()
  const navigate = useNavigate()

  const addToPipeline = async (configuration: ConfigurationRead) => {
    const body: StockItemCreate = {
      vehicleLabel: configurationLabel(configuration),
      condition: configuration.mode === 'build' ? 'new' : 'used',
      configurationId: configuration.id,
      firstRegistrationDate: configuration.firstRegistrationDate,
      odometerKm: configuration.mileageKm,
    }
    let item: StockItemRead
    try {
      item = await api.post<StockItemRead>('/inventory/stock-items', body, { 'Idempotency-Key': crypto.randomUUID() })
    } catch {
      throw new HostCommitError(t('stockList.addToPipelineError'))
    }
    overlay.pop()
    navigate(`/stock/${item.id}`)
  }

  const open = () =>
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

  return (
    <Button variant="default" leftSection={<Workflow size={16} />} onClick={open}>
      {t('stockList.addToPipeline')}
    </Button>
  )
}
