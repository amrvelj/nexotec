import { useState } from 'react'
import { Alert, Button, FileInput, Group, Image, Stack, Text } from '@mantine/core'
import { useMutation, useQuery } from '@tanstack/react-query'
import { ImageIcon } from 'lucide-react'
import { useTranslation } from 'react-i18next'
import { api } from '../../api/client'
import type { BrandingAssetKind, BrandingAssetRead, BrandingRead } from '../../api/types'
import { useCanWrite } from '../../auth/useCanWrite'
import { useFieldHint } from '../../hooks/useFieldHint'
import { apiRefusalText } from '../../utils/apiRefusal'
import { SectionCard } from '../business/SectionCard'

/** The file the API serves for a variant — its own, else the primary's. */
const brandingAssetPath = (dealershipId: string, kind: BrandingAssetKind) =>
  `/dealerships/${dealershipId}/branding/assets/${kind}`

/**
 * KAN-205 — Logo and colours › Logo: the three variants (PRD: `primary` ·
 * `mono_dark` · `favicon`; only the primary is mandatory, the others fall
 * back to it). Each uploads on its own — the file itself as the body, judged
 * by the API by its bytes — and a refusal says why in the UI language
 * (`details.reason`: JPEG, too small, …). The image is fetched through the
 * authenticated endpoint (private, no-store), held in memory, keyed by the
 * record's version so a new file replaces the old one.
 */
export function LogoSection({ branding, onWritten }: { branding: BrandingRead; onWritten: (data: BrandingRead) => void }) {
  const { t } = useTranslation()
  return (
    <SectionCard title={t('branding.logo.title')} icon={<ImageIcon size={20} />}>
      <Stack gap="lg">
        <Text c="dimmed" fz="sm">
          {t('branding.logo.formatHint')}
        </Text>
        {branding.assets.map((asset) => (
          <LogoVariant key={asset.kind} branding={branding} asset={asset} onWritten={onWritten} />
        ))}
      </Stack>
    </SectionCard>
  )
}

function LogoVariant({
  branding,
  asset,
  onWritten,
}: {
  branding: BrandingRead
  asset: BrandingAssetRead
  onWritten: (data: BrandingRead) => void
}) {
  const { t } = useTranslation()
  const hint = useFieldHint()
  const canWrite = useCanWrite('branding')
  const [file, setFile] = useState<File | null>(null)
  const [error, setError] = useState<string | null>(null)
  const path = brandingAssetPath(branding.dealershipId, asset.kind)
  const label = t(`branding.logo.kinds.${asset.kind}`)

  const image = useQuery({
    queryKey: ['branding-asset', path, branding.version],
    queryFn: () => api.getBlobUrl(path),
    enabled: asset.source !== 'none',
    staleTime: Infinity,
  })
  const done = (data: BrandingRead) => {
    setError(null)
    setFile(null)
    onWritten(data)
  }
  const fail = (err: unknown) => setError(apiRefusalText(t, err, 'branding.logo.failed'))
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

  const fileLine =
    asset.source === 'own'
      ? asset.mimeType === 'image/svg+xml'
        ? t('branding.logo.svgFile')
        : t('branding.logo.pngFile', { width: asset.width, height: asset.height })
      : asset.source === 'primary'
        ? t('branding.logo.usesPrimary')
        : t('branding.logo.none')

  return (
    <Stack gap="xs" role="group" aria-label={label}>
      <Group align="flex-start" wrap="nowrap" gap="md">
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
          {asset.source !== 'none' && image.data && <Image src={image.data} alt={label} fit="contain" h={56} w={150} />}
        </div>
        <Stack gap={2} style={{ flex: 1, minWidth: 0 }}>
          <Text fw={600} fz="sm">
            {label}
          </Text>
          <Text c="dimmed" fz="xs">
            {t(`branding.logo.kindHints.${asset.kind}`)}
          </Text>
          <Text fz="xs">{fileLine}</Text>
        </Stack>
      </Group>
      {canWrite && (
        <Group align="flex-end">
          <FileInput
            {...hint(t('branding.logo.choose'), t('branding.logo.formatHint'))}
            style={{ flex: 1 }}
            accept="image/svg+xml,image/png"
            value={file}
            onChange={setFile}
            clearable
          />
          <Button disabled={!file || upload.isPending} loading={upload.isPending} onClick={() => file && upload.mutate(file)}>
            {t('branding.logo.upload')}
          </Button>
          {asset.source === 'own' && (
            <Button variant="subtle" color="red" disabled={remove.isPending} onClick={() => remove.mutate()}>
              {t('branding.logo.remove')}
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
