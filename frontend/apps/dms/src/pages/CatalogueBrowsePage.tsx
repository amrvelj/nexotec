import { useMemo } from 'react'
import { useSearchParams } from 'react-router-dom'
import { Group, SegmentedControl, Stack, Text, Title } from '@mantine/core'
import { useTranslation } from 'react-i18next'
import { DetailTabs, useSetBreadcrumb } from '@nexotec/ui-kit'
import { MappingGapsQueue } from '../components/MappingGapsQueue'
import { CatalogueBrowseGrid } from '../components/catalogue/CatalogueBrowseGrid'
import { catalogueModeOptions } from '../catalogueOptions'
import type { CatalogueBrowseMode } from '../api/types'

const TABS = ['browse', 'mapping-gaps'] as const
type Tab = (typeof TABS)[number]

/**
 * `/catalogue` (UI/UX Spec Screen Inventory, added with C-B / FR-C-01).
 * Two tabs, `?tab=` in the URL (ADR-056):
 *   - **Browse** — drill-down + facet search against the catalogue mirror.
 *   - **Mapping gaps** — the FR-V-11 provider-code work list, folded in
 *     here from its old standalone `/vehicle-mdm/mapping-gaps` route
 *     (prototype catalogue-screen inventory item 4). Same shared
 *     `MappingGapsQueue` component, never a fork.
 */
export function CatalogueBrowsePage() {
  const { t } = useTranslation()
  useSetBreadcrumb([t('shell.nav.masterData'), t('catalogueBrowse.title')])
  const [searchParams, setSearchParams] = useSearchParams()

  const tab: Tab = TABS.includes(searchParams.get('tab') as Tab) ? (searchParams.get('tab') as Tab) : 'browse'
  const mode: CatalogueBrowseMode = searchParams.get('mode') === 'record' ? 'record' : 'build'

  const setParam = (key: string, value: string | null) => {
    setSearchParams(
      (prev) => {
        const next = new URLSearchParams(prev)
        if (value === null) next.delete(key)
        else next.set(key, value)
        return next
      },
      { replace: true },
    )
  }

  const modeOptions = useMemo(() => catalogueModeOptions(t), [t])

  return (
    <Stack gap="md">
      <Stack gap={2}>
        <Title order={2}>{t('catalogueBrowse.title')}</Title>
        <Text c="dimmed" size="sm">
          {t('catalogueBrowse.subtitle')}
        </Text>
      </Stack>

      <DetailTabs
        tabs={[
          { id: 'browse', label: t('catalogueBrowse.tabs.browse') },
          { id: 'mapping-gaps', label: t('catalogueBrowse.tabs.mappingGaps') },
        ]}
        activeTab={tab}
        onTabChange={(id) => setParam('tab', id === 'browse' ? null : id)}
      />

      {tab === 'browse' ? (
        <Stack gap="md">
          <Group gap="xs">
            <Text size="sm" fw={500}>
              {t('catalogueBrowse.mode.label')}
            </Text>
            <SegmentedControl
              size="xs"
              data={modeOptions}
              value={mode}
              onChange={(value) => setParam('mode', value === 'build' ? null : value)}
              aria-label={t('catalogueBrowse.mode.label')}
            />
          </Group>
          <CatalogueBrowseGrid mode={mode} />
        </Stack>
      ) : (
        <MappingGapsQueue paramPrefix="gaps" />
      )}
    </Stack>
  )
}
