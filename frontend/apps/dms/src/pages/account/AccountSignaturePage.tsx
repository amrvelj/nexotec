import { useEffect, useState } from 'react'
import { Alert, Button, Card, FileInput, Group, Image, Stack, Text, Textarea } from '@mantine/core'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useTranslation } from 'react-i18next'
import { useSetBreadcrumb } from '@nexotec/ui-kit'
import { api, ApiError } from '../../api/client'
import type { MeRead } from '../../api/types'
import { ME_KEY } from './accountKeys'

const MAX_BLOCK = 1000

/**
 * My account › Signature (KAN-201; Users → Personal settings: "signature
 * block and signature image"): the text lines under the user's name and
 * the image of their signature, on the documents and mails they author.
 * The image is the same one the manager can set on the Users screen
 * (KAN-200) — PNG or JPEG, at most 1 MiB, stored in the object store.
 */
export function AccountSignaturePage() {
  const { t } = useTranslation()
  useSetBreadcrumb([t('account.title'), t('account.tabs.signature')])
  const queryClient = useQueryClient()
  const me = useQuery({ queryKey: ME_KEY, queryFn: () => api.get<MeRead>('/me') })
  const image = useQuery({
    queryKey: ['me', 'signature', me.data?.version],
    queryFn: () => api.getBlobUrl('/me/signature'),
    enabled: me.data?.hasSignatureImage === true,
  })
  // The record the block was loaded from: its change and its If-Match come
  // from here, never from a later refetch (see the Profile tab).
  const [loaded, setLoaded] = useState<MeRead | null>(null)
  const [block, setBlock] = useState<string | null>(null)
  const [file, setFile] = useState<File | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [saved, setSaved] = useState(false)
  const blockChanged = (base: MeRead, text: string) => (text.trim() || null) !== (base.signatureBlock ?? null)

  useEffect(() => {
    if (!me.data) return
    if (loaded === null || block === null || (me.data.version !== loaded.version && !blockChanged(loaded, block))) {
      setLoaded(me.data)
      setBlock(me.data.signatureBlock ?? '')
    }
  }, [me.data, loaded, block])

  const say = (err: unknown) => {
    setSaved(false)
    if (err instanceof ApiError && err.status === 409) {
      setError(t('account.profile.conflict'))
      void queryClient.invalidateQueries({ queryKey: ME_KEY })
    }
    else if (err instanceof ApiError && err.status === 422) setError(t('account.signature.invalid'))
    else setError(t('common.requestFailed'))
  }
  // After our own write: the block's baseline moves to what we saved; an
  // image change moves only the version, so an open block edit stays valid.
  const done = (data: MeRead, blockSaved: boolean) => {
    queryClient.setQueryData(ME_KEY, data)
    setLoaded((base) => (blockSaved || base === null ? data : { ...base, version: data.version, hasSignatureImage: data.hasSignatureImage }))
    setError(null)
    setSaved(true)
  }
  const saveBlock = useMutation({
    mutationFn: (current: MeRead) =>
      api.patch<MeRead>('/me', { signatureBlock: (block ?? '').trim() || null }, { 'If-Match': String(current.version) }),
    onSuccess: (data) => {
      done(data, true)
      setBlock(data.signatureBlock ?? '')
    },
    onError: say,
  })
  const upload = useMutation({
    mutationFn: ({ current, image: chosen }: { current: MeRead; image: File }) =>
      api.putBinary<MeRead>('/me/signature', chosen, chosen.type, { 'If-Match': String(current.version) }),
    onSuccess: (data) => {
      done(data, false)
      setFile(null)
    },
    onError: say,
  })
  const remove = useMutation({
    mutationFn: (current: MeRead) => api.delete<MeRead>('/me/signature', { 'If-Match': String(current.version) }),
    onSuccess: (data) => done(data, false),
    onError: say,
  })

  if (me.isError) return <Alert color="red" role="alert">{t('common.requestFailed')}</Alert>
  if (!loaded || block === null) return null
  const current = loaded
  const blockDirty = blockChanged(loaded, block)

  return (
    <Stack gap="lg">
      {error && (
        <Alert color="red" role="alert" withCloseButton closeButtonLabel={t('common.dismiss')} onClose={() => setError(null)}>
          {error}
        </Alert>
      )}
      {saved && (
        <Alert color="green" role="status">
          {t('account.signature.saved')}
        </Alert>
      )}
      <Card withBorder radius="md" padding="lg">
        <form
          id="account-signature-block"
          onSubmit={(event) => {
            event.preventDefault()
            if (blockDirty && !saveBlock.isPending) saveBlock.mutate(current)
          }}
        >
          <Stack gap="md">
            <Textarea
              label={t('account.signature.block')}
              description={t('account.signature.blockHint')}
              autosize
              minRows={3}
              maxLength={MAX_BLOCK}
              value={block}
              onChange={(e) => {
                setBlock(e.currentTarget.value)
                setSaved(false)
              }}
            />
            <Group justify="flex-end">
              <Button type="submit" form="account-signature-block" disabled={!blockDirty} loading={saveBlock.isPending}>
                {t('common.save')}
              </Button>
            </Group>
          </Stack>
        </form>
      </Card>
      <Card withBorder radius="md" padding="lg">
        <Stack gap="md">
          <Text fw={600}>{t('account.signature.image')}</Text>
          {current.hasSignatureImage && image.data ? (
            <Group align="flex-end">
              <Image src={image.data} alt={t('account.signature.current')} h={72} w="auto" fit="contain" />
              <Button
                type="button"
                variant="subtle"
                size="xs"
                color="red"
                loading={remove.isPending}
                onClick={() => remove.mutate(current)}
              >
                {t('account.signature.remove')}
              </Button>
            </Group>
          ) : (
            <Text c="dimmed" fz="sm">
              {t('account.signature.noImage')}
            </Text>
          )}
          <Group align="flex-end">
            <FileInput
              style={{ flex: 1 }}
              label={t('account.signature.upload')}
              description={t('account.signature.imageHint')}
              accept="image/png,image/jpeg"
              value={file}
              onChange={(chosen) => {
                setFile(chosen)
                setSaved(false)
              }}
              clearable
            />
            <Button
              type="button"
              disabled={!file}
              loading={upload.isPending}
              onClick={() => file && upload.mutate({ current, image: file })}
            >
              {t('account.signature.uploadButton')}
            </Button>
          </Group>
        </Stack>
      </Card>
    </Stack>
  )
}
