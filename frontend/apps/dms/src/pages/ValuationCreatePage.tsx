import { useState } from 'react'
import { useLocation, useNavigate, useSearchParams } from 'react-router-dom'
import { useQuery } from '@tanstack/react-query'
import { Button, Group, Loader, Stack } from '@mantine/core'
import { useSetBreadcrumb } from '@nexotec/ui-kit'
import { useTranslation } from 'react-i18next'
import { api } from '../api/client'
import { ValuationCreateDialog } from '../components/ValuationCreateDialog'
import { ConfiguratorOverlay } from '../components/configurator/ConfiguratorOverlay'
import type { ConfigurationRead, ValuationRead } from '../api/types'

/** `/valuations/new` (optionally `?supersedes=<id>` for "Neu bewerten") —
 * a dedicated route rather than a modal state on the list page, so a
 * direct link/refresh lands here too, matching OfferCreateRedirectPage's
 * own routing shape for the analogous "new draft" entry point.
 *
 * C-F (KAN-10, FR-C-14): a new valuation first captures the car in the
 * configurator, **`record` mode only** — one input identifies it from a
 * plate, VIN or Typenschein — then the valuation dialog opens prefilled
 * and carries the configuration. From the list the configurator is an
 * overlay (`NewValuationButton`) and the configuration arrives here in the
 * navigation state; a direct link captures it on this page first.
 * Entering the car by hand stays available; a revaluation ("Neu bewerten")
 * copies its predecessor and skips this step.
 */
export function ValuationCreatePage() {
  const { t } = useTranslation()
  const navigate = useNavigate()
  const [searchParams] = useSearchParams()
  const supersedesId = searchParams.get('supersedes')

  useSetBreadcrumb([t('shell.nav.valuations'), t('valuationCreate.title')])

  const supersedesQuery = useQuery({
    queryKey: ['valuation', supersedesId],
    queryFn: () => api.get<ValuationRead>(`/valuations/${supersedesId}`),
    enabled: Boolean(supersedesId),
  })

  const location = useLocation()
  const handedOver = (location.state as { configuration?: ConfigurationRead } | null)?.configuration ?? null
  const [step, setStep] = useState<'configure' | 'form'>(supersedesId || handedOver ? 'form' : 'configure')
  const [configuration, setConfiguration] = useState<ConfigurationRead | null>(handedOver)

  if (supersedesId && supersedesQuery.isLoading) return <Loader />

  if (step === 'configure') {
    return (
      <Stack gap="xs">
        <ConfiguratorOverlay
          allowedModes={['record']}
          onCommitted={(captured) => {
            setConfiguration(captured)
            setStep('form')
          }}
          onClose={() => navigate('/valuations')}
        />
        <Group justify="center">
          <Button variant="subtle" onClick={() => setStep('form')}>
            {t('valuationCreate.configurator.skip')}
          </Button>
        </Group>
      </Stack>
    )
  }

  return (
    <ValuationCreateDialog
      key={configuration?.id ?? 'by-hand'}
      configuration={configuration}
      opened
      onClose={() => navigate('/valuations')}
      onCreated={(valuation) => navigate(`/valuations/${valuation.id}`, { replace: true })}
      supersedes={supersedesQuery.data ?? null}
    />
  )
}
