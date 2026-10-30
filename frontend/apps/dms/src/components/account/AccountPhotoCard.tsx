import { useState } from 'react'
import { Alert, Button, Card, FileInput, Group, Stack, Text } from '@mantine/core'
import { useMutation, useQueryClient } from '@tanstack/react-query'
import { useTranslation } from 'react-i18next'
import { api } from '../../api/client'
import type { MeRead } from '../../api/types'
import { useFieldHint } from '../../hooks/useFieldHint'
import { ME_KEY } from '../../pages/account/accountKeys'
import { apiRefusalText } from '../../utils/apiRefusal'
import { UserAvatar } from '../users/UserAvatar'

/**
 * KAN-401 — My account › Profile: the user's own photo, the same one the
 * manager sets on the Users screen (PNG or JPEG, at most 5 MiB, in the
 * object store). An upload or a removal stands on its own and moves the
 * record's version on; `onWritten` hands the new record to the profile
 * form, so its own save does not meet a stale version.
 */
export function AccountPhotoCard({ me, onWritten }: { me: MeRead; onWritten: (data: MeRead) => void }) {
  const { t } = useTranslation()
  const hint = useFieldHint()
  const queryClient = useQueryClient()
  const [file, setFile] = useState<File | null>(null)
  const [error, setError] = useState<string | null>(null)
  const done = (data: MeRead) => {
    queryClient.setQueryData(ME_KEY, data)
    onWritten(data)
    setError(null)
    setFile(null)
  }
  const fail = (err: unknown) => setError(apiRefusalText(t, err, 'account.photo.failed'))
  const upload = useMutation({
    mutationFn: (chosen: File) => api.putBinary<MeRead>('/me/photo', chosen, chosen.type, { 'If-Match': String(me.version) }),
    onSuccess: done,
    onError: fail,
  })
  const remove = useMutation({
    mutationFn: () => api.delete<MeRead>('/me/photo', { 'If-Match': String(me.version) }),
    onSuccess: done,
    onError: fail,
  })
  const name = `${me.firstName} ${me.lastName}`.trim()

  return (
    <Card withBorder radius="md" padding="lg">
      <Stack gap="md">
        <Text fw={600} size="sm">
          {t('account.photo.title')}
        </Text>
        {error && (
          <Alert color="red" role="alert">
            {error}
          </Alert>
        )}
        <Group align="flex-end">
          <UserAvatar photoPath="/me/photo" hasPhoto={me.hasPhoto ?? false} version={me.version} name={name} size="xl" />
          <FileInput {...hint(t('account.photo.choose'), t('account.photo.hint'))}
            accept="image/png,image/jpeg"
            value={file}
            onChange={setFile}
            clearable
          />
          <Button disabled={!file || upload.isPending} loading={upload.isPending} onClick={() => file && upload.mutate(file)}>
            {t('account.photo.upload')}
          </Button>
          {me.hasPhoto && (
            <Button variant="subtle" color="red" disabled={remove.isPending} onClick={() => remove.mutate()}>
              {t('account.photo.remove')}
            </Button>
          )}
        </Group>
      </Stack>
    </Card>
  )
}
