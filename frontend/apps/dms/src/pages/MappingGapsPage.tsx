import { Stack, Title } from '@mantine/core'
import { useTranslation } from 'react-i18next'
import { useSetBreadcrumb } from '@nexotec/ui-kit'
import { MappingGapsQueue } from '../components/MappingGapsQueue'

/**
 * Standalone deep-link view of the mapping-gap queue
 * (`/vehicle-mdm/mapping-gaps`). The queue itself now also renders as a
 * section of `/settings/reference` (UI spec Screen Inventory — "the
 * mapping-gap queue is a work list" on the reference-data admin screen);
 * both surfaces render the one `MappingGapsQueue` component, never a fork.
 */
export function MappingGapsPage() {
  const { t } = useTranslation()
  useSetBreadcrumb([t('shell.nav.masterData'), t('mappingGaps.title')])

  return (
    <Stack gap="md">
      <Title order={2}>{t('mappingGaps.title')}</Title>
      <MappingGapsQueue />
    </Stack>
  )
}
