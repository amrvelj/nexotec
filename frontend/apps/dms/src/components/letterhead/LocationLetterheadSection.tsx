import { useState } from 'react'
import { Alert, Anchor, Button, Card, FileInput, Group, Image, Stack, Text } from '@mantine/core'
import { useMutation, useQuery } from '@tanstack/react-query'
import { MapPin } from 'lucide-react'
import { useTranslation } from 'react-i18next'
import { Link } from 'react-router-dom'
import { api } from '../../api/client'
import type { BrandingRead, LocationRead } from '../../api/types'
import { useCanWrite } from '../../auth/useCanWrite'
import { useFieldHint } from '../../hooks/useFieldHint'
import { apiRefusalText } from '../../utils/apiRefusal'

const locationLogoPath = (dealershipId: string, locationId: string) =>
  `/dealerships/${dealershipId}/branding/locations/${locationId}/logo`

/**
 * KAN-206 (PRD: a per-location override of the address block and logo only,
 * A-27) — the letterhead of the location chosen in the preview: its address
 * block, which is the free text kept on the location itself (Q3 A), and its
 * own logo, uploaded here like the Betrieb's (the branding write, If-Match on
 * the Betrieb's version). The footer never changes per location.
 */
export function LocationLetterheadSection({
  location,
  branding,
  onWritten,
}: {
  location: LocationRead | null
  branding: BrandingRead | null
  onWritten: (data: BrandingRead) => void
}) {
  const { t } = useTranslation()
  return (
    <Card withBorder radius="md" padding="lg" component="section" aria-label={t('letterhead.location.title')}>
      <Group gap="sm" mb="md">
        <MapPin size={20} />
        <Text component="h3" fw={600} fz="lg" m={0}>
          {location ? `${t('letterhead.location.title')} · ${location.name}` : t('letterhead.location.title')}
        </Text>
      </Group>
      <Stack gap="md">
        <Text c="dimmed" fz="sm">
          {t('letterhead.location.explanation')}
        </Text>
        {!location && <Text fz="sm">{t('letterhead.location.pick')}</Text>}
        {location && (
          <>
            <Stack gap={4}>
              <Text fw={600} fz="sm">
                {t('letterhead.location.addressBlock')}
              </Text>
              <Text fz="sm" style={{ whiteSpace: 'pre-line' }}>
                {location.letterheadAddressOverride?.trim() || t('letterhead.location.addressBlockNone')}
              </Text>
              <Anchor component={Link} to={`/administration/locations/${location.id}`} fz="sm">
                {t('letterhead.location.addressBlockEdit')}
              </Anchor>
            </Stack>
            {branding && <LocationLogo key={location.id} location={location} branding={branding} onWritten={onWritten} />}
          </>
        )}
      </Stack>
    </Card>
  )
}

function LocationLogo({
  location,
  branding,
  onWritten,
}: {
  location: LocationRead
  branding: BrandingRead
  onWritten: (data: BrandingRead) => void
}) {
  const { t } = useTranslation()
  const hint = useFieldHint()
  const canWrite = useCanWrite('branding')
  const [file, setFile] = useState<File | null>(null)
  const [error, setError] = useState<string | null>(null)
  const path = locationLogoPath(branding.dealershipId, location.id)
  const own = (branding.locationLogos ?? []).some((logo) => logo.locationId === location.id)
  const label = t('letterhead.location.logo')

  const image = useQuery({
    queryKey: ['location-logo', path, branding.version],
    queryFn: () => api.getBlobUrl(path),
    enabled: own,
    staleTime: Infinity,
  })
  const done = (data: BrandingRead) => {
    setError(null)
    setFile(null)
    onWritten(data)
  }
  const fail = (err: unknown) => setError(apiRefusalText(t, err, 'letterhead.location.failed'))
  const upload = useMutation({
    mutationFn: (chosen: File) =>
      api.putBinary<BrandingRead>(path, chosen, chosen.type || 'application/octet-stream', { 'If-Match': String(branding.version) }),
    onSuccess: done,
    onError: fail,
  })
  const remove = useMutation({
    mutationFn: () => api.delete<BrandingRead>(path, { 'If-Match': String(branding.version) }),
    onSuccess: done,
    onError: fail,
  })

  return (
    <Stack gap="xs" role="group" aria-label={label}>
      <Text fw={600} fz="sm">
        {label}
      </Text>
      <Group align="center" gap="md" wrap="nowrap">
        <div
          style={{
            width: 160,
            height: 64,
            flexShrink: 0,
            display: 'flex',
            alignItems: 'center',
            justifyContent: 'center',
            border: '1px dashed var(--mantine-color-gray-4)',
            borderRadius: 'var(--mantine-radius-md)',
            background: 'var(--mantine-color-white)',
          }}
        >
          {own && image.data && <Image src={image.data} alt={label} fit="contain" h={56} w={150} />}
        </div>
        <Text fz="xs">{own ? t('letterhead.location.logoOwn') : t('letterhead.location.logoNone')}</Text>
      </Group>
      {canWrite && (
        <Group align="flex-end">
          <FileInput
            {...hint(t('letterhead.location.choose'), t('branding.logo.formatHint'))}
            style={{ flex: 1 }}
            accept="image/svg+xml,image/png"
            value={file}
            onChange={setFile}
            clearable
          />
          <Button disabled={!file || upload.isPending} loading={upload.isPending} onClick={() => file && upload.mutate(file)}>
            {t('letterhead.location.upload')}
          </Button>
          {own && (
            <Button variant="subtle" color="red" disabled={remove.isPending} onClick={() => remove.mutate()}>
              {t('letterhead.location.remove')}
            </Button>
          )}
        </Group>
      )}
      {error && (
        <Alert color="red" role="alert" withCloseButton closeButtonLabel={t('common.dismiss')} onClose={() => setError(null)}>
          {error}
        </Alert>
      )}
    </Stack>
  )
}
