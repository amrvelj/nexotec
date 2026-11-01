import { useSearchParams } from 'react-router-dom'
import { Badge, Group, Stack, Tabs, Text, Title } from '@mantine/core'
import { ShieldCheck } from 'lucide-react'
import { useTranslation } from 'react-i18next'
import { slate, useSetBreadcrumb } from '@nexotec/ui-kit'
import { TRANSLATION_SOURCES } from '../components/translations/translationSources'

/**
 * KAN-209 — Platform Administration › Translations (`/platform/translations`;
 * D-A-23, FR-A-29…32): the one panel where platform staff edit the texts
 * Nexotec ships, in DE/FR/IT/EN, served at runtime. One tab per source
 * (`translationSources.ts`); the chosen tab is in the URL. Never role-hidden:
 * anyone may open it and read, the API lets only `platform_admin` write.
 */
export function TranslationsPage() {
  const { t } = useTranslation()
  useSetBreadcrumb([t('shell.nav.administration'), t('translations.title')])
  const [searchParams, setSearchParams] = useSearchParams()
  const requested = searchParams.get('source')
  const source = TRANSLATION_SOURCES.find((s) => s.id === requested) ?? TRANSLATION_SOURCES[0]

  const choose = (id: string | null) => {
    if (!id || id === source.id) return
    // A source's own filters never carry over to another's.
    setSearchParams(new URLSearchParams({ source: id }), { replace: true })
  }

  return (
    <Stack gap="lg">
      <Group justify="space-between" align="flex-start">
        <Stack gap={2}>
          <Title order={2}>{t('translations.title')}</Title>
          <Text c={slate[5]} size="sm">
            {t('translations.subtitle')}
          </Text>
        </Stack>
        <Badge color="grape" variant="light" leftSection={<ShieldCheck size={14} />}>
          {t('translations.badge')}
        </Badge>
      </Group>
      <Tabs value={source.id} onChange={choose} keepMounted={false}>
        <Tabs.List aria-label={t('translations.sourcesLabel')}>
          {TRANSLATION_SOURCES.map((s) => (
            <Tabs.Tab key={s.id} value={s.id}>
              {t(s.labelKey)}
            </Tabs.Tab>
          ))}
        </Tabs.List>
        {TRANSLATION_SOURCES.map((s) => (
          <Tabs.Panel key={s.id} value={s.id} pt="md">
            <s.Panel />
          </Tabs.Panel>
        ))}
      </Tabs>
    </Stack>
  )
}
