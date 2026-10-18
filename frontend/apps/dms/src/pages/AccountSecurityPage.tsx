import { useState } from 'react'
import { Alert, Badge, Button, Card, Code, Group, Stack, Table, Text, TextInput, Title } from '@mantine/core'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { KeyRound, ShieldCheck, Smartphone, Trash2 } from 'lucide-react'
import { useTranslation } from 'react-i18next'
import { useSetBreadcrumb } from '@nexotec/ui-kit'
import { api, ApiError } from '../api/client'
import type {
  MfaStatusRead,
  PasskeyOptionsResponse,
  PasskeyRead,
  RecoveryCodesRead,
  SessionList,
  TotpEnrolmentRead,
} from '../api/types'
import { useAuth } from '../auth/AuthContext'
import { createPasskey, passkeysSupported } from '../auth/webauthn'
import { formatDateTime } from '../utils/format'

const MFA_KEY = ['auth', 'mfa'] as const
const SESSIONS_KEY = ['auth', 'sessions'] as const

/**
 * My account › Security (KAN-196; D-A-09, D-A-36, A-13, FR-AU-17): the
 * user's own second factors — an authenticator app with its recovery codes,
 * passkeys — and the sessions open on their account. The one screen a user
 * with `mfaEnrolmentRequired` can reach until a factor is enrolled.
 */
export function AccountSecurityPage() {
  const { t } = useTranslation()
  useSetBreadcrumb([t('auth.security.breadcrumb'), t('auth.security.title')])
  const { mfaEnrolmentRequired, refresh } = useAuth()
  const queryClient = useQueryClient()
  const status = useQuery({ queryKey: MFA_KEY, queryFn: () => api.get<MfaStatusRead>('/auth/mfa') })
  const sessions = useQuery({ queryKey: SESSIONS_KEY, queryFn: () => api.get<SessionList>('/auth/sessions') })

  const [enrolment, setEnrolment] = useState<TotpEnrolmentRead | null>(null)
  const [code, setCode] = useState('')
  const [recoveryCodes, setRecoveryCodes] = useState<string[] | null>(null)
  const [passkeyLabel, setPasskeyLabel] = useState('')
  const [error, setError] = useState<string | null>(null)

  const say = (err: unknown) => setError(err instanceof ApiError ? err.message : t('common.requestFailed'))
  const afterFactorChange = async () => {
    await queryClient.invalidateQueries({ queryKey: MFA_KEY })
    await refresh()
  }

  const beginTotp = useMutation({
    mutationFn: () => api.post<TotpEnrolmentRead>('/auth/mfa/totp/enrol'),
    onSuccess: (data) => {
      setEnrolment(data)
      setCode('')
      setError(null)
    },
    onError: say,
  })
  const confirmTotp = useMutation({
    mutationFn: () => api.post<RecoveryCodesRead>('/auth/mfa/totp/confirm', { code }),
    onSuccess: async (data) => {
      setRecoveryCodes(data.codes)
      setEnrolment(null)
      setError(null)
      await afterFactorChange()
    },
    onError: (err) => setError(err instanceof ApiError && err.status === 422 ? t('auth.login.codeInvalid') : (err as Error).message),
  })
  const removeTotp = useMutation({
    mutationFn: () => api.delete('/auth/mfa/totp'),
    onSuccess: async () => {
      setRecoveryCodes(null)
      await afterFactorChange()
    },
    onError: say,
  })
  const regenerate = useMutation({
    mutationFn: () => api.post<RecoveryCodesRead>('/auth/mfa/recovery-codes/regenerate'),
    onSuccess: async (data) => {
      setRecoveryCodes(data.codes)
      await queryClient.invalidateQueries({ queryKey: MFA_KEY })
    },
    onError: say,
  })
  const addPasskey = useMutation({
    mutationFn: async () => {
      const options = await api.post<PasskeyOptionsResponse>('/auth/mfa/passkeys/options')
      const credential = await createPasskey(options.options)
      return api.post<PasskeyRead>('/auth/mfa/passkeys', {
        challengeId: options.challengeId,
        credential,
        label: passkeyLabel.trim(),
      })
    },
    onSuccess: async () => {
      setPasskeyLabel('')
      setError(null)
      await afterFactorChange()
    },
    onError: (err) => setError(err instanceof ApiError ? err.message : t('auth.security.passkeys.failed')),
  })
  const removePasskey = useMutation({
    mutationFn: (id: string) => api.delete(`/auth/mfa/passkeys/${id}`),
    onSuccess: afterFactorChange,
    onError: say,
  })
  const endSession = useMutation({
    mutationFn: (id: string) => api.delete(`/auth/sessions/${id}`),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: SESSIONS_KEY }),
    onError: say,
  })

  const data = status.data

  return (
    <Stack gap="lg" p="md" maw={880}>
      <Title order={2}>{t('auth.security.title')}</Title>

      {mfaEnrolmentRequired && (
        <Alert color="yellow" icon={<ShieldCheck size={18} />}>
          {t('auth.security.enrolmentRequired')}
        </Alert>
      )}
      {error && (
        <Alert color="red" role="alert">
          {error}
        </Alert>
      )}

      <Card withBorder radius="md" padding="lg">
        <Stack gap="md">
          <Group justify="space-between" align="flex-start">
            <Group gap="sm">
              <Smartphone size={20} />
              <div>
                <Text fw={600}>{t('auth.security.totp.title')}</Text>
                <Text c="dimmed" fz="sm">
                  {data?.totpEnrolled ? t('auth.security.totp.enrolled') : t('auth.security.totp.notEnrolled')}
                </Text>
              </div>
            </Group>
            {data?.totpEnrolled ? (
              <Button
                variant="default"
                leftSection={<Trash2 size={16} />}
                onClick={() => removeTotp.mutate()}
                loading={removeTotp.isPending}
              >
                {t('auth.security.totp.remove')}
              </Button>
            ) : (
              !enrolment && (
                <Button onClick={() => beginTotp.mutate()} loading={beginTotp.isPending} disabled={!data}>
                  {t('auth.security.totp.enrol')}
                </Button>
              )
            )}
          </Group>

          {enrolment && (
            <Stack gap="sm">
              <Text fz="sm">{t('auth.security.totp.step1')}</Text>
              <Code block>{enrolment.secret}</Code>
              <Text fz="xs" c="dimmed" style={{ wordBreak: 'break-all' }}>
                {enrolment.provisioningUri}
              </Text>
              <Text fz="sm">{t('auth.security.totp.step2')}</Text>
              <form
                onSubmit={(event) => {
                  event.preventDefault()
                  confirmTotp.mutate()
                }}
                noValidate
              >
                <Group align="flex-end">
                  <TextInput
                    label={t('auth.security.totp.code')}
                    value={code}
                    onChange={(e) => setCode(e.currentTarget.value)}
                    inputMode="numeric"
                    autoComplete="one-time-code"
                    required
                  />
                  <Button type="submit" loading={confirmTotp.isPending}>
                    {t('auth.security.totp.confirm')}
                  </Button>
                </Group>
              </form>
            </Stack>
          )}

          {data && (data.totpEnrolled || data.passkeys.length > 0) && (
            <Group justify="space-between">
              <Text fz="sm">{t('auth.security.recovery.remaining', { count: data.recoveryCodesRemaining })}</Text>
              <Button variant="subtle" size="xs" onClick={() => regenerate.mutate()} loading={regenerate.isPending}>
                {t('auth.security.recovery.regenerate')}
              </Button>
            </Group>
          )}

          {recoveryCodes && (
            <Alert color="blue" title={t('auth.security.recovery.title')}>
              <Text fz="sm" mb="xs">
                {t('auth.security.recovery.saveHint')}
              </Text>
              <Code block>{recoveryCodes.join('\n')}</Code>
              <Button mt="sm" size="xs" variant="default" onClick={() => setRecoveryCodes(null)}>
                {t('auth.security.recovery.done')}
              </Button>
            </Alert>
          )}
        </Stack>
      </Card>

      <Card withBorder radius="md" padding="lg">
        <Stack gap="md">
          <Group gap="sm">
            <KeyRound size={20} />
            <div>
              <Text fw={600}>{t('auth.security.passkeys.title')}</Text>
              <Text c="dimmed" fz="sm">
                {data && data.passkeys.length === 0 ? t('auth.security.passkeys.none') : null}
              </Text>
            </div>
          </Group>
          {data && data.passkeys.length > 0 && (
            <Table>
              <Table.Tbody>
                {data.passkeys.map((passkey) => (
                  <Table.Tr key={passkey.id}>
                    <Table.Td>{passkey.label || t('auth.security.passkeys.unnamed')}</Table.Td>
                    <Table.Td>
                      <Text fz="sm" c="dimmed">
                        {passkey.lastUsedAt
                          ? t('auth.security.passkeys.lastUsed', { when: formatDateTime(passkey.lastUsedAt) })
                          : t('auth.security.passkeys.never')}
                      </Text>
                    </Table.Td>
                    <Table.Td align="right">
                      <Button
                        variant="subtle"
                        size="xs"
                        color="red"
                        onClick={() => removePasskey.mutate(passkey.id)}
                        loading={removePasskey.isPending}
                      >
                        {t('common.remove')}
                      </Button>
                    </Table.Td>
                  </Table.Tr>
                ))}
              </Table.Tbody>
            </Table>
          )}
          {passkeysSupported() ? (
            <form
              onSubmit={(event) => {
                event.preventDefault()
                addPasskey.mutate()
              }}
              noValidate
            >
              <Group align="flex-end">
                <TextInput
                  label={t('auth.security.passkeys.label')}
                  placeholder={t('auth.security.passkeys.labelPlaceholder')}
                  value={passkeyLabel}
                  onChange={(e) => setPasskeyLabel(e.currentTarget.value)}
                  maxLength={100}
                />
                <Button type="submit" variant="default" loading={addPasskey.isPending} disabled={!data}>
                  {t('auth.security.passkeys.add')}
                </Button>
              </Group>
            </form>
          ) : (
            <Text fz="sm" c="dimmed">
              {t('auth.security.passkeys.unsupported')}
            </Text>
          )}
        </Stack>
      </Card>

      <Card withBorder radius="md" padding="lg">
        <Stack gap="md">
          <Text fw={600}>{t('auth.security.sessions.title')}</Text>
          <Table>
            <Table.Thead>
              <Table.Tr>
                <Table.Th>{t('auth.security.sessions.device')}</Table.Th>
                <Table.Th>{t('auth.security.sessions.lastSeen')}</Table.Th>
                <Table.Th>{t('auth.security.sessions.expires')}</Table.Th>
                <Table.Th />
              </Table.Tr>
            </Table.Thead>
            <Table.Tbody>
              {(sessions.data?.items ?? []).map((session) => (
                <Table.Tr key={session.id}>
                  <Table.Td>
                    <Group gap="xs">
                      <Text fz="sm" lineClamp={1} style={{ maxWidth: 320 }}>
                        {session.deviceSummary || session.ipAddress}
                      </Text>
                      {session.current && <Badge size="sm">{t('auth.security.sessions.current')}</Badge>}
                    </Group>
                  </Table.Td>
                  <Table.Td>{formatDateTime(session.lastSeenAt)}</Table.Td>
                  <Table.Td>{formatDateTime(session.absoluteExpiresAt)}</Table.Td>
                  <Table.Td align="right">
                    {!session.current && (
                      <Button
                        variant="subtle"
                        size="xs"
                        onClick={() => endSession.mutate(session.id)}
                        loading={endSession.isPending}
                      >
                        {t('auth.security.sessions.end')}
                      </Button>
                    )}
                  </Table.Td>
                </Table.Tr>
              ))}
            </Table.Tbody>
          </Table>
        </Stack>
      </Card>
    </Stack>
  )
}
