import { useEffect } from 'react'
import { Alert, Group, SegmentedControl, Select, Stack, Text } from '@mantine/core'
import { useQuery } from '@tanstack/react-query'
import { useTranslation } from 'react-i18next'
import { DocumentPreview } from '@nexotec/ui-kit'
import { api } from '../../api/client'
import type { LocationRead } from '../../api/types'

export type PreviewDocument = 'offer' | 'invoice'
const DOCUMENTS: PreviewDocument[] = ['offer', 'invoice']

interface Props {
  dealershipId: string
  /** Only those who may edit the template may preview it: it prints the real IBAN. */
  allowed: boolean
  templateVersion: number
  brandingVersion: number | null
  document: PreviewDocument
  onDocumentChange: (document: PreviewDocument) => void
  locations: LocationRead[]
  locationId: string | null
  onLocationChange: (locationId: string | null) => void
}

/**
 * KAN-206 (FR-A-06) — the one `DocumentPreview` of the template: a sample
 * offer or invoice as the server's one renderer prints it
 * (`GET …/document-template/preview`, ADR-051 — never a second, client-side
 * renderer), for the Betrieb or one of its locations. Keyed by the
 * template's and the branding's versions, so every saved change renders it
 * again.
 *
 * KAN-719: the renderer also prints what other screens edit — the Betrieb's
 * master data (e-mail, website, addresses), a location's address block, the
 * default bank account. The cache entry lives only while this screen shows
 * it (`gcTime: 0`) and every mount fetches it again (`refetchOnMount`), so
 * coming back to the screen always renders afresh and
 * never shows a PDF those edits made stale — nor a blob: URL the unmount
 * already revoked.
 */
export function LetterheadPreview({
  dealershipId,
  allowed,
  templateVersion,
  brandingVersion,
  document,
  onDocumentChange,
  locations,
  locationId,
  onLocationChange,
}: Props) {
  const { t } = useTranslation()
  const params = new URLSearchParams({ document })
  if (locationId) params.set('location', locationId)
  const preview = useQuery({
    queryKey: ['document-template-preview', dealershipId, templateVersion, brandingVersion, document, locationId],
    queryFn: () => api.getBlobUrl(`/dealerships/${dealershipId}/document-template/preview?${params.toString()}`),
    enabled: allowed && dealershipId !== '',
    staleTime: Infinity,
    gcTime: 0,
    refetchOnMount: 'always',
  })
  // The previous render's blob: URL goes once a newer one replaces it.
  useEffect(() => {
    const url = preview.data
    return () => {
      if (url) URL.revokeObjectURL(url)
    }
  }, [preview.data])

  if (!allowed) {
    return (
      <Alert color="blue" variant="light">
        {t('letterhead.preview.notAllowed')}
      </Alert>
    )
  }

  return (
    <Stack gap="xs">
      <Group grow align="flex-end">
        <SegmentedControl
          aria-label={t('letterhead.preview.document')}
          value={document}
          onChange={(value) => onDocumentChange(value as PreviewDocument)}
          data={DOCUMENTS.map((value) => ({ value, label: t(`letterhead.preview.documents.${value}`) }))}
        />
        <Select
          label={t('letterhead.preview.location')}
          value={locationId ?? ''}
          onChange={(value) => onLocationChange(value ? value : null)}
          allowDeselect={false}
          data={[
            { value: '', label: t('letterhead.preview.locationNone') },
            ...locations.map((location) => ({ value: location.id, label: location.name })),
          ]}
        />
      </Group>
      <DocumentPreview
        src={preview.data ?? null}
        title={t('letterhead.preview.title')}
        loading={preview.isLoading}
        loadingLabel={t('letterhead.preview.loading')}
        error={preview.isError ? t('letterhead.preview.error') : null}
      />
      <Text c="dimmed" fz="xs">
        {t('letterhead.preview.note')}
      </Text>
    </Stack>
  )
}
