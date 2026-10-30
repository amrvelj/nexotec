import { useState } from 'react'
import { useLocation, useNavigate, useSearchParams } from 'react-router-dom'
import { useQuery } from '@tanstack/react-query'
import { Alert, Button, Group, Loader, Stack } from '@mantine/core'
import { useSetBreadcrumb } from '@nexotec/ui-kit'
import { useTranslation } from 'react-i18next'
import { api } from '../api/client'
import { apiRefusalText } from '../utils/apiRefusal'
import { ValuationCreateDialog } from '../components/ValuationCreateDialog'
import { ConfiguratorOverlay } from '../components/configurator/ConfiguratorOverlay'
import type { CatalogueVariantRead, ConfigurationRead, ValuationRead, VehicleMdmRead } from '../api/types'

/** `/valuations/new` (optionally `?supersedes=<id>` for "Neu bewerten", or
 * `?vehicle=<id>` for «Request valuation» on Vehicle 360, KAN-468) —
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
 * copies its predecessor and skips this step. Requested from a vehicle, the
 * capture starts from that car's VIN and, if skipped, the dialog takes its
 * VIN and first registration, and the make, model and trim of the catalogue
 * variant it is matched to (Anto's option A on KAN-468; an unmatched car has
 * none); closing goes back to the vehicle.
 */
export function ValuationCreatePage() {
  const { t } = useTranslation()
  const navigate = useNavigate()
  const [searchParams] = useSearchParams()
  const supersedesId = searchParams.get('supersedes')
  const vehicleId = searchParams.get('vehicle')

  useSetBreadcrumb([t('shell.nav.valuations'), t('valuationCreate.title')])

  const supersedesQuery = useQuery({
    queryKey: ['valuation', supersedesId],
    queryFn: () => api.get<ValuationRead>(`/valuations/${supersedesId}`),
    enabled: Boolean(supersedesId),
  })
  const vehicleQuery = useQuery({
    queryKey: ['vehicle-mdm', vehicleId],
    queryFn: () => api.get<VehicleMdmRead>(`/vehicle-mdm/${vehicleId}`),
    enabled: Boolean(vehicleId),
  })
  const variantId = vehicleQuery.data?.catalogueVariantId ?? null
  const variantQuery = useQuery({
    queryKey: ['catalogue-variant', variantId],
    queryFn: () => api.get<CatalogueVariantRead>(`/catalogue/variants/${variantId}`),
    enabled: Boolean(variantId),
  })
  const vehicle = vehicleQuery.data
    ? {
        vin: vehicleQuery.data.vin,
        firstRegistrationDate: vehicleQuery.data.firstRegistrationDate,
        make: variantQuery.data?.brandDisplayName ?? null,
        model: variantQuery.data?.modelGroupName ?? null,
        trim: variantQuery.data?.variantName ?? null,
      }
    : null
  const close = () => navigate(vehicleId ? `/vehicles/${vehicleId}` : '/valuations')

  const location = useLocation()
  const handedOver = (location.state as { configuration?: ConfigurationRead } | null)?.configuration ?? null
  const [step, setStep] = useState<'configure' | 'form'>(supersedesId || handedOver ? 'form' : 'configure')
  const [configuration, setConfiguration] = useState<ConfigurationRead | null>(handedOver)

  if (supersedesId && supersedesQuery.isLoading) return <Loader />
  if (vehicleId && (vehicleQuery.isLoading || (variantId && variantQuery.isLoading))) return <Loader />

  if (step === 'configure') {
    return (
      <Stack gap="xs">
        {vehicleId && vehicleQuery.isError && (
          <Alert color="yellow" role="alert">
            {apiRefusalText(t, vehicleQuery.error, 'valuationCreate.vehicleNotLoaded')}
          </Alert>
        )}
        <ConfiguratorOverlay
          allowedModes={['record']}
          initialVin={vehicle?.vin}
          onCommitted={(captured) => {
            setConfiguration(captured)
            setStep('form')
          }}
          onClose={close}
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
      vehicle={vehicle}
      opened
      onClose={close}
      onCreated={(valuation) => navigate(`/valuations/${valuation.id}`, { replace: true })}
      supersedes={supersedesQuery.data ?? null}
    />
  )
}
