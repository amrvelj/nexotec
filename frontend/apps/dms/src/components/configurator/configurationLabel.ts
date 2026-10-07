import type { ConfigurationRead } from '../../api/types'

/** The one display label of a configuration — brand · model group · variant,
 * the same composition the backend stores as a host's `configurationLabel`
 * (`app/vehicle/services/configuration_host.py::configuration_label`). */
export function configurationLabel(c: Pick<ConfigurationRead, 'brandDisplayName' | 'modelGroupName' | 'variantName' | 'catalogueVariantLabel'>): string {
  const label = [c.brandDisplayName, c.modelGroupName, c.variantName].filter(Boolean).join(' ')
  return (label || c.catalogueVariantLabel || '—').slice(0, 200)
}
