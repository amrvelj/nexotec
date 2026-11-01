import { useEffect } from 'react'
import { Stack, Text } from '@mantine/core'
import { useQuery } from '@tanstack/react-query'
import { useTranslation } from 'react-i18next'
import { DocumentPreview } from '@nexotec/ui-kit'
import { api } from '../../api/client'
import type { BrandingRead } from '../../api/types'

/**
 * KAN-205 — the live preview of the document header: the PDF the one
 * renderer produces (`GET …/branding/preview`, ADR-051 — never a second,
 * client-side renderer), shown in the kit's `DocumentPreview`. Keyed by the
 * record's version, so every save — a logo, a colour, a placement — renders
 * it again.
 */
export function BrandingPreview({ branding }: { branding: BrandingRead }) {
  const { t } = useTranslation()
  const preview = useQuery({
    queryKey: ['branding-preview', branding.dealershipId, branding.version],
    queryFn: () => api.getBlobUrl(`/dealerships/${branding.dealershipId}/branding/preview`),
    staleTime: Infinity,
  })
  // The previous render's blob: URL goes once a newer one replaces it.
  useEffect(() => {
    const url = preview.data
    return () => {
      if (url) URL.revokeObjectURL(url)
    }
  }, [preview.data])

  return (
    <Stack gap="xs">
      <DocumentPreview
        src={preview.data ?? null}
        title={t('branding.preview.title')}
        loading={preview.isLoading}
        loadingLabel={t('branding.preview.loading')}
        error={preview.isError ? t('branding.preview.error') : null}
      />
      <Text c="dimmed" fz="xs">
        {t('branding.preview.note')}
      </Text>
    </Stack>
  )
}
