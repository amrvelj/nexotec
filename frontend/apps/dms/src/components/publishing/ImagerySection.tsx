import { useState } from 'react'
import { ActionIcon, Alert, Badge, Button, FileInput, Group, Image, Stack, Text, Tooltip } from '@mantine/core'
import { useMutation, useQuery } from '@tanstack/react-query'
import { ArrowDown, ArrowUp, Images, Star, Trash2 } from 'lucide-react'
import { useTranslation } from 'react-i18next'
import { api } from '../../api/client'
import type { PublishingImageKind, PublishingImageRead, PublishingProfileRead } from '../../api/types'
import { apiRefusalText } from '../../utils/apiRefusal'
import { SectionCard } from '../business/SectionCard'

const KINDS: PublishingImageKind[] = ['premises', 'team', 'hero']

/**
 * KAN-225 — a site's photos for the website (PRD Imagery): premises, team
 * and a hero image, each ordered with one primary. JPEG or PNG, judged by
 * the API by their bytes; read through the authenticated GET.
 */
export function ImagerySection({
  profile,
  locationId,
  canWrite,
  onWritten,
}: {
  profile: PublishingProfileRead
  locationId: string
  canWrite: boolean
  onWritten: (data: PublishingProfileRead) => void
}) {
  const { t } = useTranslation()
  const [error, setError] = useState<string | null>(null)
  const ifMatch = { 'If-Match': String(profile.version) }
  const base = `/dealerships/${profile.dealershipId}/publishing-profile`
  const done = (data: PublishingProfileRead) => {
    setError(null)
    onWritten(data)
  }
  const fail = (err: unknown) => setError(apiRefusalText(t, err, 'publishing.imagery.failed'))
  const upload = useMutation({
    mutationFn: ({ kind, file }: { kind: PublishingImageKind; file: File }) =>
      api.postBinary<PublishingProfileRead>(`${base}/locations/${locationId}/images/${kind}`, file, file.type || 'image/jpeg', ifMatch),
    onSuccess: done,
    onError: fail,
  })
  const reorder = useMutation({
    mutationFn: ({ kind, ids }: { kind: PublishingImageKind; ids: string[] }) =>
      api.put<PublishingProfileRead>(`${base}/locations/${locationId}/images/${kind}/order`, { imageIds: ids }, ifMatch),
    onSuccess: done,
    onError: fail,
  })
  const primary = useMutation({
    mutationFn: (id: string) => api.post<PublishingProfileRead>(`${base}/images/${id}/primary`, undefined, ifMatch),
    onSuccess: done,
    onError: fail,
  })
  const remove = useMutation({
    mutationFn: (id: string) => api.delete<PublishingProfileRead>(`${base}/images/${id}`, ifMatch),
    onSuccess: done,
    onError: fail,
  })
  const images = profile.images.filter((image) => image.locationId === locationId)

  return (
    <SectionCard title={t('publishing.sections.imagery')} icon={<Images size={20} />}>
      <Stack gap="md" id="publishing-imagery">
        <Text c="dimmed" fz="sm">
          {t('publishing.imagery.hint')}
        </Text>
        {KINDS.map((kind) => {
          const ofKind = images.filter((image) => image.kind === kind).sort((a, b) => a.position - b.position)
          const move = (index: number, by: number) => {
            const ids = ofKind.map((image) => image.id)
            const [moved] = ids.splice(index, 1)
            ids.splice(index + by, 0, moved)
            reorder.mutate({ kind, ids })
          }
          return (
            <Stack key={kind} gap="xs" role="group" aria-label={t(`publishing.imagery.kinds.${kind}`)}>
              <Text fw={600} fz="sm">
                {t(`publishing.imagery.kinds.${kind}`)}
              </Text>
              {ofKind.length === 0 && (
                <Text fz="xs" c="dimmed">
                  {t('publishing.imagery.none')}
                </Text>
              )}
              <Group gap="sm">
                {ofKind.map((image, index) => (
                  <ImageTile
                    key={image.id}
                    image={image}
                    path={`${base}/images/${image.id}`}
                    canWrite={canWrite}
                    first={index === 0}
                    last={index === ofKind.length - 1}
                    onUp={() => move(index, -1)}
                    onDown={() => move(index, 1)}
                    onPrimary={() => primary.mutate(image.id)}
                    onRemove={() => remove.mutate(image.id)}
                  />
                ))}
              </Group>
              {canWrite && <UploadRow loading={upload.isPending} onUpload={(file) => upload.mutate({ kind, file })} />}
            </Stack>
          )
        })}
        {error && (
          <Alert color="red" role="alert">
            {error}
          </Alert>
        )}
      </Stack>
    </SectionCard>
  )
}

function UploadRow({ loading, onUpload }: { loading: boolean; onUpload: (file: File) => void }) {
  const { t } = useTranslation()
  const [file, setFile] = useState<File | null>(null)
  return (
    <Group gap="xs">
      <FileInput
        size="xs"
        accept="image/jpeg,image/png"
        placeholder={t('publishing.imagery.choose')}
        aria-label={t('publishing.imagery.choose')}
        value={file}
        onChange={setFile}
        clearable
      />
      <Button
        size="xs"
        disabled={!file}
        loading={loading}
        onClick={() => {
          if (file) onUpload(file)
          setFile(null)
        }}
      >
        {t('publishing.imagery.upload')}
      </Button>
    </Group>
  )
}

function ImageTile({
  image,
  path,
  canWrite,
  first,
  last,
  onUp,
  onDown,
  onPrimary,
  onRemove,
}: {
  image: PublishingImageRead
  path: string
  canWrite: boolean
  first: boolean
  last: boolean
  onUp: () => void
  onDown: () => void
  onPrimary: () => void
  onRemove: () => void
}) {
  const { t } = useTranslation()
  const src = useQuery({ queryKey: ['publishing-image', image.id], queryFn: () => api.getBlobUrl(path), staleTime: Infinity })
  return (
    <Stack gap={4} align="center" style={{ width: 132 }}>
      <div style={{ width: 132, height: 88, border: '1px solid var(--mantine-color-gray-3)', borderRadius: 'var(--mantine-radius-sm)', overflow: 'hidden' }}>
        {src.data && <Image src={src.data} alt={t(`publishing.imagery.kinds.${image.kind}`)} h={88} w={132} fit="cover" />}
      </div>
      {image.isPrimary && (
        <Badge size="xs" variant="light">
          {t('publishing.imagery.primary')}
        </Badge>
      )}
      {canWrite && (
        <Group gap={2}>
          <Tooltip label={t('publishing.imagery.moveUp')}>
            <ActionIcon variant="subtle" size="sm" disabled={first} aria-label={t('publishing.imagery.moveUp')} onClick={onUp}>
              <ArrowUp size={14} />
            </ActionIcon>
          </Tooltip>
          <Tooltip label={t('publishing.imagery.moveDown')}>
            <ActionIcon variant="subtle" size="sm" disabled={last} aria-label={t('publishing.imagery.moveDown')} onClick={onDown}>
              <ArrowDown size={14} />
            </ActionIcon>
          </Tooltip>
          <Tooltip label={t('publishing.imagery.makePrimary')}>
            <ActionIcon variant="subtle" size="sm" disabled={image.isPrimary} aria-label={t('publishing.imagery.makePrimary')} onClick={onPrimary}>
              <Star size={14} />
            </ActionIcon>
          </Tooltip>
          <Tooltip label={t('publishing.imagery.remove')}>
            <ActionIcon variant="subtle" size="sm" color="red" aria-label={t('publishing.imagery.remove')} onClick={onRemove}>
              <Trash2 size={14} />
            </ActionIcon>
          </Tooltip>
        </Group>
      )}
    </Stack>
  )
}
