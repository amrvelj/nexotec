import { Alert, Badge, Group, Image, SimpleGrid, Stack, Text } from '@mantine/core'
import { Info, Plug } from 'lucide-react'
import { useTranslation } from 'react-i18next'
import type { CatalogueSpecificationRead } from '../../api/types'

// BildTyp (small/large) and BildArt (exterior/interior) are FR-C-09's own
// fixed, documented two-value codes — unlike an axle or option code, there
// is no open-ended "variant" bucket here, so an unrecognised value (which
// would mean the provider added a third code this UI doesn't know about
// yet) falls back to the raw code rather than a translation.
const BILD_TYP_KEY: Record<string, string> = { S: 'small', L: 'large' }
const BILD_ART_KEY: Record<string, string> = { A: 'exterior', I: 'interior' }

function codeLabel(t: (key: string) => string, prefix: string, map: Record<string, string>, code: string): string {
  const key = map[code]
  return key ? t(`configurator.images.${prefix}.${key}`) : code
}

export interface ImagesTabProps {
  /** `undefined` while loading or for a manual (off-catalogue) draft. */
  spec: CatalogueSpecificationRead | undefined
  isLoading: boolean
  isManual: boolean
}

/**
 * FR-C-09 — small(210px)/large(1200px), exterior/interior. Entitlement-
 * gated: absent permission means a placeholder, never a broken image and
 * never a fabricated "upload" affordance — no upload endpoint exists
 * anywhere in this codebase yet (a genuine backend gap, not this ticket's
 * to fill), so the entitlement-denied state is the explanatory message
 * alone, matching OptionsTab's own `options-entitlement-notice` pattern.
 */
export function ImagesTab({ spec, isLoading, isManual }: ImagesTabProps) {
  const { t } = useTranslation()

  if (isManual) {
    return (
      <Alert color="gray" icon={<Info size={16} />} data-testid="images-no-catalogue-variant">
        {t('configurator.images.noCatalogueVariant')}
      </Alert>
    )
  }

  if (isLoading || !spec) {
    return (
      <Text size="sm" c="dimmed">
        {t('common.loading')}
      </Text>
    )
  }

  if (!spec.imagesAvailable) {
    return (
      <Alert
        color="yellow"
        icon={<Plug size={16} />}
        title={t('configurator.images.unavailable.title')}
        data-testid="images-entitlement-notice"
      >
        <Stack gap={4}>
          <Text size="sm">{t('configurator.images.unavailable.body')}</Text>
          {/* `dealerCanUploadImages` is the API's own marker for this
           * exact case (see CatalogueSpecificationRead's docstring) —
           * consumed here rather than re-deriving "not entitled" from
           * imagesAvailable alone, so this stays correct if a future
           * backend change ever decouples the two. No upload control is
           * offered — no upload endpoint exists anywhere in this
           * codebase yet (a genuine gap, not this ticket's to fill). */}
          {spec.dealerCanUploadImages && (
            <Text size="xs" c="dimmed">
              {t('configurator.images.unavailable.uploadNote')}
            </Text>
          )}
        </Stack>
      </Alert>
    )
  }

  if (spec.images.length === 0) {
    return (
      <Alert color="gray" icon={<Info size={16} />}>
        {t('configurator.images.empty')}
      </Alert>
    )
  }

  return (
    <SimpleGrid cols={{ base: 2, sm: 3 }} spacing="md" data-testid="images-tab">
      {[...spec.images]
        .sort((a, b) => a.sequence - b.sequence)
        .map((image) => (
          <Stack key={image.imageKey} gap={4} data-testid={`image-${image.imageKey}`}>
            {image.imageUrl ? (
              <Image
                src={image.imageUrl}
                alt={image.imageKey}
                radius="sm"
                h={image.bildTyp === 'S' ? 120 : 280}
                fit="cover"
                fallbackSrc="data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' width='210' height='120'%3E%3Crect width='210' height='120' fill='%23e9ecef'/%3E%3C/svg%3E"
              />
            ) : (
              <Text size="xs" c="dimmed">
                {image.imageKey}
              </Text>
            )}
            <Group gap={4}>
              {image.bildArt && (
                <Badge size="xs" variant="light">
                  {codeLabel(t, 'bildArt', BILD_ART_KEY, image.bildArt)}
                </Badge>
              )}
              {image.bildTyp && (
                <Badge size="xs" variant="outline">
                  {codeLabel(t, 'bildTyp', BILD_TYP_KEY, image.bildTyp)}
                </Badge>
              )}
            </Group>
          </Stack>
        ))}
    </SimpleGrid>
  )
}
