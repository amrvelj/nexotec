import { Alert, Loader } from '@mantine/core'
import { useQuery } from '@tanstack/react-query'
import { useTranslation } from 'react-i18next'
import { api } from '../../api/client'
import { ConfigurationSummaryCard } from './ConfigurationSummaryCard'
import type { ConfigurationRead } from '../../api/types'

export interface HostConfigurationCardProps {
  configurationId: string
  /** Offered only where the host lets the advisor edit (the offer workspace). */
  onOpenConfigurator?: (configuration: ConfigurationRead) => void
}

/**
 * C-F (KAN-10) — how a host that stores a `configurationId` shows it: it
 * loads the configuration and renders **the** `ConfigurationSummaryCard`.
 * Four hosts, one card; this wrapper only fetches.
 * `configurationHosts.test.ts` asserts no host renders a configuration any
 * other way.
 */
export function HostConfigurationCard({ configurationId, onOpenConfigurator }: HostConfigurationCardProps) {
  const { t } = useTranslation()
  const query = useQuery({
    queryKey: ['configuration', configurationId],
    queryFn: () => api.get<ConfigurationRead>(`/configurations/${configurationId}`),
  })
  if (query.isLoading) return <Loader size="sm" />
  if (query.isError || !query.data) return <Alert color="red">{t('vehicleDetail.specification.loadError')}</Alert>
  const configuration = query.data
  return (
    <ConfigurationSummaryCard
      configuration={configuration}
      onOpenConfigurator={onOpenConfigurator ? () => onOpenConfigurator(configuration) : undefined}
    />
  )
}
