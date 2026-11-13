import { Alert, Button, Group, SegmentedControl, Stack, Text, Title } from '@mantine/core'
import { PencilRuler } from 'lucide-react'
import { useTranslation } from 'react-i18next'
import { CatalogueBrowseGrid } from '../catalogue/CatalogueBrowseGrid'
import { IdentificationPanel, type IdentifiedStart } from './IdentificationPanel'
import { configuratorModeOptions, type ConfiguratorMode } from '../../configurationOptions'
import type { CatalogueVariantRead, ConfigurationRead } from '../../api/types'

interface ConfiguratorFindPhaseProps {
  allowedModes: ConfiguratorMode[]
  mode: ConfiguratorMode
  setMode: (mode: ConfiguratorMode) => void
  error: string | null
  startFromIdentification: (start: IdentifiedStart) => void
  onReuse: (configuration: ConfigurationRead) => void
  setVinInput: (query: string) => void
  identificationPrefill: string
  startManual: () => void
  startFromVariant: (variant: CatalogueVariantRead) => void
  onClose: () => void
}

/** Phase 1 of `ConfiguratorOverlay`: find the car (identify, browse, or start
 * a manual configuration). State and handlers stay in the overlay. */
export function ConfiguratorFindPhase({
  allowedModes,
  mode,
  setMode,
  error,
  startFromIdentification,
  onReuse,
  setVinInput,
  identificationPrefill,
  startManual,
  startFromVariant,
  onClose,
}: ConfiguratorFindPhaseProps) {
  const { t } = useTranslation()
  return (
    <Stack gap="lg" p="xl" maw={1100} mx="auto">
      <Title order={3}>{t('configurator.find.title')}</Title>

      {allowedModes.length > 1 && (
        <Group gap="xs">
          <Text size="sm" fw={500}>
            {t('configurator.mode.label')}
          </Text>
          <SegmentedControl
            size="xs"
            data={configuratorModeOptions(t).filter((o) => allowedModes.includes(o.value))}
            value={mode}
            onChange={(v) => setMode(v as ConfiguratorMode)}
            aria-label={t('configurator.mode.label')}
          />
        </Group>
      )}

      {error && <Alert color="red">{error}</Alert>}

      <IdentificationPanel
        allowedModes={allowedModes}
        onStart={startFromIdentification}
        onReuse={onReuse}
        onQueryChange={setVinInput}
        initialQuery={identificationPrefill}
      />

      <Group>
        <Button
          variant="light"
          leftSection={<PencilRuler size={16} />}
          onClick={startManual}
        >
          {t('configurator.find.manual')}
        </Button>
      </Group>

      <Text fw={600} size="sm">
        {t('configurator.find.browse')}
      </Text>
      <CatalogueBrowseGrid mode={mode} onSelectVariant={startFromVariant} />

      <Group justify="flex-end">
        <Button variant="default" onClick={onClose}>
          {t('common.cancel')}
        </Button>
      </Group>
    </Stack>
  )
}
