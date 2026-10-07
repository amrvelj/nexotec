import { Button } from '@mantine/core'
import { useNavigate } from 'react-router-dom'
import { useTranslation } from 'react-i18next'
import { useOverlay } from '@nexotec/ui-kit'
import { ConfiguratorOverlay } from './configurator/ConfiguratorOverlay'

/**
 * C-F (KAN-10, FR-C-14) — "From the Valuation list, New valuation": the
 * configurator opens as an overlay over the list (ADR-059), **`record` mode
 * only**. The captured configuration then travels to `/valuations/new`,
 * whose dialog opens prefilled with it.
 */
export function NewValuationButton() {
  const { t } = useTranslation()
  const overlay = useOverlay()
  const navigate = useNavigate()

  const open = () =>
    overlay.push({
      key: 'configurator-new-valuation',
      content: (
        <ConfiguratorOverlay
          allowedModes={['record']}
          onCommitted={(configuration) => {
            overlay.pop()
            navigate('/valuations/new', { state: { configuration } })
          }}
          onClose={() => overlay.pop()}
        />
      ),
    })

  return <Button onClick={open}>{t('valuationsList.newValuation')}</Button>
}
