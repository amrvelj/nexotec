import { useState } from 'react'
import { Alert, Grid, Skeleton, Stack, Text, Title } from '@mantine/core'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useTranslation } from 'react-i18next'
import { useSetBreadcrumb } from '@nexotec/ui-kit'
import { api } from '../api/client'
import type { BrandingRead, DocumentTemplateRead, DocumentTemplateUpdate, LocationList } from '../api/types'
import { useAuth } from '../auth/AuthContext'
import { useCanWrite } from '../auth/useCanWrite'
import { LetterheadPreview, type PreviewDocument } from '../components/letterhead/LetterheadPreview'
import { LocationLetterheadSection } from '../components/letterhead/LocationLetterheadSection'
import { TemplateLayoutSection } from '../components/letterhead/TemplateLayoutSection'
import { apiRefusalText } from '../utils/apiRefusal'

const documentTemplateKey = (dealershipId: string) => ['document-template', dealershipId] as const
const brandingKey = (dealershipId: string) => ['branding', dealershipId] as const

/**
 * Dealer Administration › Letterhead and template (KAN-206; PRD "Letterhead
 * and the document template"; A-RULE-0: the one place the Betrieb configures
 * how its documents print). The template's layout — logo position and size,
 * the envelope window's side, the footer's master-data columns, the draft
 * watermark, page numbers — saves on every change (FR-A-06), beside one
 * `DocumentPreview` of a sample offer or invoice rendered by the one server
 * renderer (ADR-051 — never a second, client-side one), for the Betrieb or
 * one of its locations. A location's own letterhead logo is managed here;
 * its address block is the free text on the location itself (Q3 A). The
 * preview prints the real bank account, so only those who may edit the
 * template see it; everyone else reads the settings.
 */
export function LetterheadPage() {
  const { t } = useTranslation()
  useSetBreadcrumb([t('shell.nav.administration'), t('letterhead.title')])
  const { activeDealership } = useAuth()
  const dealershipId = activeDealership?.id ?? ''
  const queryClient = useQueryClient()
  const canWrite = useCanWrite('document_templates')
  const [document, setDocument] = useState<PreviewDocument>('offer')
  const [locationId, setLocationId] = useState<string | null>(null)
  const [saveError, setSaveError] = useState<string | null>(null)

  const template = useQuery({
    queryKey: documentTemplateKey(dealershipId),
    queryFn: () => api.get<DocumentTemplateRead>(`/dealerships/${dealershipId}/document-template`),
    enabled: dealershipId !== '',
  })
  const locations = useQuery({
    queryKey: ['locations', dealershipId, 'letterhead'],
    queryFn: () => api.get<LocationList>(`/dealerships/${dealershipId}/locations`),
    enabled: dealershipId !== '',
  })
  const branding = useQuery({
    queryKey: brandingKey(dealershipId),
    queryFn: () => api.get<BrandingRead>(`/dealerships/${dealershipId}/branding`),
    enabled: dealershipId !== '',
  })

  // Every change is one PATCH with the version the last one returned; the
  // controls are disabled while it runs, so two never race (If-Match).
  const save = useMutation({
    mutationFn: (changes: DocumentTemplateUpdate) =>
      api.patch<DocumentTemplateRead>(`/dealerships/${dealershipId}/document-template`, changes, {
        'If-Match': String(template.data?.version ?? 0),
      }),
    onMutate: () => setSaveError(null),
    onSuccess: (data) => queryClient.setQueryData(documentTemplateKey(dealershipId), data),
    onError: (err) => setSaveError(apiRefusalText(t, err, 'letterhead.saveError')),
  })

  const selectedLocation = locations.data?.items.find((location) => location.id === locationId) ?? null

  return (
    <Stack gap="md">
      <div>
        <Title order={2}>{t('letterhead.title')}</Title>
        <Text c="dimmed" fz="sm">
          {t('letterhead.subtitle')}
        </Text>
      </div>
      {template.isError && (
        <Alert color="red" role="alert">
          {t('letterhead.loadError')}
        </Alert>
      )}
      {template.isLoading && <Skeleton height={320} radius="md" />}
      {template.data && (
        <Grid gutter="md">
          <Grid.Col span={{ base: 12, lg: 6 }}>
            <Stack gap="md">
              {!canWrite && (
                <Text c="dimmed" fz="sm">
                  {t('letterhead.readOnly')}
                </Text>
              )}
              <TemplateLayoutSection
                template={template.data}
                canWrite={canWrite}
                saving={save.isPending}
                saved={save.isSuccess}
                error={saveError}
                onDismissError={() => setSaveError(null)}
                onChange={(changes) => save.mutate(changes)}
              />
              <LocationLetterheadSection
                location={selectedLocation}
                branding={branding.data ?? null}
                onWritten={(data) => queryClient.setQueryData(brandingKey(dealershipId), data)}
              />
            </Stack>
          </Grid.Col>
          <Grid.Col span={{ base: 12, lg: 6 }}>
            <LetterheadPreview
              dealershipId={dealershipId}
              allowed={canWrite}
              templateVersion={template.data.version}
              brandingVersion={branding.data?.version ?? null}
              document={document}
              onDocumentChange={setDocument}
              locations={locations.data?.items ?? []}
              locationId={locationId}
              onLocationChange={setLocationId}
            />
          </Grid.Col>
        </Grid>
      )}
    </Stack>
  )
}
