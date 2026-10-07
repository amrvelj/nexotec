import { Alert, Loader, Text } from '@mantine/core'
import { useQuery } from '@tanstack/react-query'
import { useTranslation } from 'react-i18next'
import { api, ApiError } from '../../api/client'
import { ConfigurationSummaryCard } from '../configurator/ConfigurationSummaryCard'
import type { ConfigurationRead } from '../../api/types'

/**
 * FR-C-15 — Vehicle 360's Specification tab: this dealership's configuration
 * of the vehicle, **read-only**, through the one shared summary card (no
 * "open configurator" here). A configuration is tenant-private, so another
 * dealership's configuration of the same car is never shown; "none" is the
 * ordinary state for a car this dealership never configured.
 */
export function SpecificationTab({ vehicleId }: { vehicleId: string }) {
  const { t } = useTranslation()
  const query = useQuery({
    queryKey: ['vehicle-mdm', vehicleId, 'configuration'],
    queryFn: async () => {
      try {
        return await api.get<ConfigurationRead>(`/vehicle-mdm/${vehicleId}/configuration`)
      } catch (err) {
        if (err instanceof ApiError && err.status === 404) return null
        throw err
      }
    },
  })
  if (query.isLoading) return <Loader size="sm" />
  if (query.isError) return <Alert color="red">{t('vehicleDetail.specification.loadError')}</Alert>
  if (!query.data) return <Text c="dimmed">{t('vehicleDetail.specification.empty')}</Text>
  return <ConfigurationSummaryCard configuration={query.data} />
}
