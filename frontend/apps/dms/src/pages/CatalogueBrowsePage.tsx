import { useMemo } from 'react'
import { useSearchParams } from 'react-router-dom'
import { Group, SegmentedControl, Stack, Text, Title } from '@mantine/core'
import { useTranslation } from 'react-i18next'
import { DetailTabs, useSetBreadcrumb } from '@nexotec/ui-kit'
import { MappingGapsQueue } from '../components/MappingGapsQueue'
import { BrandsAdmin } from '../components/catalogue/BrandsAdmin'
import { ProviderCaptionsPanel } from '../components/catalogue/ProviderCaptionsPanel'
import { useAuth } from '../auth/AuthContext'
import { CatalogueBrowseGrid } from '../components/catalogue/CatalogueBrowseGrid'
import { catalogueModeOptions } from '../catalogueOptions'
import type { CatalogueBrowseMode } from '../api/types'

const TABS = ['browse', 'mapping-gaps', 'brands', 'captions'] as const
type Tab = (typeof TABS)[number]

/**
 * `/catalogue` (UI/UX Spec Screen Inventory, added with C-B / FR-C-01).
 * Three tabs, `?tab=` in the URL (ADR-056):
 *   - **Browse** — drill-down + facet search against the catalogue mirror.
 *   - **Mapping gaps** — the FR-V-11 provider-code work list, folded in
 *     here from its old standalone `/vehicle-mdm/mapping-gaps` route
 *     (prototype catalogue-screen inventory item 4). Same shared
 *     `MappingGapsQueue` component, never a fork.
 *   - **Brands** (KAN-448) — create and edit the platform's brands
 *     (ADR-075), shown to platform staff only: the brand write is theirs, and
 *     a link to `?tab=brands` from anyone else opens Browse.
 */
export function CatalogueBrowsePage() {
  const { t } = useTranslation()
  useSetBreadcrumb([t('shell.nav.masterData'), t('catalogueBrowse.title')])
  const [searchParams, setSearchParams] = useSearchParams()
  const { user } = useAuth()
  const isPlatformStaff = user?.accessRoles.includes('platform_admin') ?? false

  const requested = searchParams.get('tab') as Tab
  // KAN-588: the provider vocabulary is platform staff's, like the brands.
  const staffOnly = requested === 'brands' || requested === 'captions'
  const tab: Tab = TABS.includes(requested) && (!staffOnly || isPlatformStaff) ? requested : 'browse'
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
          ...(isPlatformStaff
            ? [
                { id: 'brands', label: t('catalogueBrowse.tabs.brands') },
                { id: 'captions', label: t('catalogueBrowse.tabs.captions') },
              ]
            : []),
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
      ) : tab === 'brands' ? (
        <BrandsAdmin />
      ) : tab === 'captions' ? (
        <ProviderCaptionsPanel />
      ) : (
        <MappingGapsQueue paramPrefix="gaps" />
      )}
    </Stack>
  )
}
