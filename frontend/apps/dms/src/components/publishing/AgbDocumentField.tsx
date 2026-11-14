import { useState } from 'react'
import { Alert, Button, FileInput, Group, Stack, Text } from '@mantine/core'
import { useMutation } from '@tanstack/react-query'
import { useTranslation } from 'react-i18next'
import { api } from '../../api/client'
import type { PublishingProfileRead } from '../../api/types'
import { apiRefusalText } from '../../utils/apiRefusal'
import { formatDate } from '../../utils/format'

/** KAN-225 — the AGB as an uploaded PDF (PRD: «uploaded PDF or hosted URL»),
 * kept in the object store and read through the authenticated GET. */
export function AgbDocumentField({
  profile,
  canWrite,
  onWritten,
}: {
  profile: PublishingProfileRead
  canWrite: boolean
  onWritten: (data: PublishingProfileRead) => void
}) {
  const { t } = useTranslation()
  const [file, setFile] = useState<File | null>(null)
  const [error, setError] = useState<string | null>(null)
  const path = `/dealerships/${profile.dealershipId}/publishing-profile/agb`
  const ifMatch = { 'If-Match': String(profile.version) }
  const done = (data: PublishingProfileRead) => {
    setError(null)
    setFile(null)
    onWritten(data)
  }
  const fail = (err: unknown) => setError(apiRefusalText(t, err, 'publishing.agb.failed'))
  const upload = useMutation({
    mutationFn: (chosen: File) => api.putBinary<PublishingProfileRead>(path, chosen, 'application/pdf', ifMatch),
    onSuccess: done,
    onError: fail,
  })
  const remove = useMutation({
    mutationFn: () => api.delete<PublishingProfileRead>(path, ifMatch),
    onSuccess: done,
    onError: fail,
  })
  const open = async () => {
    const url = await api.getBlobUrl(path)
    window.open(url, '_blank', 'noopener')
  }
  const document = profile.agbDocument
  return (
    <Stack gap={4} role="group" aria-label={t('publishing.agb.label')}>
      <Text fw={500} fz="sm">
        {t('publishing.agb.label')}
      </Text>
      <Text fz="xs" c="dimmed">
        {document
          ? t('publishing.agb.present', { date: formatDate(document.uploadedAt), size: Math.ceil(document.byteSize / 1024) })
          : t('publishing.agb.none')}
      </Text>
      <Group gap="xs">
        {document && (
          <Button variant="subtle" size="xs" onClick={() => void open()}>
            {t('publishing.agb.open')}
          </Button>
        )}
        {canWrite && (
          <>
            <FileInput
              size="xs"
              accept="application/pdf"
              placeholder={t('publishing.agb.choose')}
              aria-label={t('publishing.agb.choose')}
              value={file}
              onChange={setFile}
              clearable
            />
            <Button size="xs" disabled={!file} loading={upload.isPending} onClick={() => file && upload.mutate(file)}>
              {t('publishing.agb.upload')}
            </Button>
            {document && (
              <Button size="xs" variant="subtle" color="red" loading={remove.isPending} onClick={() => remove.mutate()}>
                {t('publishing.agb.remove')}
              </Button>
            )}
          </>
        )}
      </Group>
      {error && (
        <Alert color="red" role="alert">
          {error}
        </Alert>
      )}
    </Stack>
  )
}
