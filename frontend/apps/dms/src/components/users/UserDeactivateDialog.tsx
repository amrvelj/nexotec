import { useEffect, useMemo, useState } from 'react'
import { Alert, Button, Checkbox, Group, Modal, Select, Stack, Table, Text, Textarea } from '@mantine/core'
import { useQuery } from '@tanstack/react-query'
import { UserX } from 'lucide-react'
import { useTranslation } from 'react-i18next'
import { api, ApiError } from '../../api/client'
import type { OwnershipRead, OwnershipTransferRequest, UserDeactivateRequest, UserDeactivatedRead, UserRead } from '../../api/types'
import { useIdempotencyKey } from '../../hooks/useIdempotencyKey'
import { ownedGroupLabel, userDisplayName } from '../../userOptions'

export interface UserDeactivateDialogProps {
  opened: boolean
  onClose: () => void
  dealershipId: string
  user: UserRead | null
  /** Active colleagues who can receive the leaver's records. */
  colleagues: UserRead[]
  onDeactivated: (result: UserDeactivatedRead) => void
}

const UNASSIGNED = '__unassigned__'

/**
 * KAN-200 — deactivation with the reassignment step (FR-A-37, D-A-35).
 * What the person still owns is listed by module; the manager hands each
 * group to a colleague or leaves it unassigned (the module's own queue).
 * Every group needs a decision before the button works — nothing is left
 * silently with the leaver. A confirmation modal, as the UI spec reserves
 * for an irreversible action; there is no delete (A-RULE-5).
 */
export function UserDeactivateDialog({ opened, onClose, dealershipId, user, colleagues, onDeactivated }: UserDeactivateDialogProps) {
  const { t } = useTranslation()
  const [reason, setReason] = useState('')
  const [endEmployment, setEndEmployment] = useState(true)
  const [choices, setChoices] = useState<Record<string, string | null>>({})
  const [submitting, setSubmitting] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const idempotency = useIdempotencyKey()

  const ownership = useQuery({
    queryKey: ['users', dealershipId, user?.id, 'ownership'],
    queryFn: () => api.get<OwnershipRead>(`/dealerships/${dealershipId}/users/${user!.id}/ownership`),
    enabled: opened && user !== null,
  })

  useEffect(() => {
    if (!opened) return
    setReason('')
    setEndEmployment(true)
    setChoices({})
    setError(null)
    idempotency.renew()
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [opened, user?.id])

  const colleagueOptions = useMemo(
    () => [
      { value: UNASSIGNED, label: t('usersAdmin.deactivate.leaveUnassigned') },
      ...colleagues
        .filter((c) => c.id !== user?.id && (c.status === 'active' || c.status === 'invited'))
        .map((c) => ({ value: c.id, label: userDisplayName(c) })),
    ],
    [colleagues, user?.id, t],
  )

  const groups = ownership.data?.groups ?? []
  const key = (module: string, group: string) => `${module}/${group}`
  const undecided = groups.filter((g) => choices[key(g.module, g.group)] === undefined)
  const canSubmit = reason.trim().length > 0 && undecided.length === 0 && ownership.isSuccess && !submitting

  const submit = async () => {
    if (!user) return
    setSubmitting(true)
    setError(null)
    const transfers: OwnershipTransferRequest[] = groups.map((g) => {
      const choice = choices[key(g.module, g.group)]
      return { module: g.module, group: g.group, toUserId: choice === UNASSIGNED || !choice ? null : choice }
    })
    const body: UserDeactivateRequest = {
      reason: reason.trim(),
      transfers,
      ...(endEmployment ? { employmentStatus: 'terminated' as const } : {}),
    }
    try {
      const result = await api.post<UserDeactivatedRead>(`/dealerships/${dealershipId}/users/${user.id}/deactivate`, body, {
        ...idempotency.headers(body),
        'If-Match': String(user.version),
      })
      idempotency.renew()
      onDeactivated(result)
    } catch (err) {
      if (err instanceof ApiError && err.status === 400) setError(err.message) // the last-manager rule, said as the API says it
      else if (err instanceof ApiError && err.status === 409) setError(t('usersAdmin.form.conflict'))
      else if (err instanceof ApiError) setError(err.message)
      else setError(t('common.requestFailed'))
      void ownership.refetch()
    } finally {
      setSubmitting(false)
    }
  }

  return (
    <Modal opened={opened} onClose={onClose} title={user ? t('usersAdmin.deactivate.title', { name: userDisplayName(user) }) : ''} size="lg">
      <Stack gap="md">
        <Text size="sm">{t('usersAdmin.deactivate.intro')}</Text>
        {error && (
          <Alert color="red" role="alert">
            {error}
          </Alert>
        )}
        {ownership.isError && (
          <Alert color="red">
            {t('usersAdmin.deactivate.ownershipLoadError')}
            <Button size="xs" variant="default" ml="sm" onClick={() => void ownership.refetch()}>
              {t('common.retry')}
            </Button>
          </Alert>
        )}

        <Text fw={600} size="sm">
          {t('usersAdmin.deactivate.reassignTitle')}
        </Text>
        {ownership.isSuccess && groups.length === 0 && (
          <Text size="sm" c="dimmed">
            {t('usersAdmin.deactivate.nothingOpen', { modules: ownership.data.modules.length })}
          </Text>
        )}
        {groups.length > 0 && (
          <Table>
            <Table.Thead>
              <Table.Tr>
                <Table.Th>{t('usersAdmin.deactivate.columns.what')}</Table.Th>
                <Table.Th>{t('usersAdmin.deactivate.columns.count')}</Table.Th>
                <Table.Th>{t('usersAdmin.deactivate.columns.transferTo')}</Table.Th>
              </Table.Tr>
            </Table.Thead>
            <Table.Tbody>
              {groups.map((g) => (
                <Table.Tr key={key(g.module, g.group)}>
                  <Table.Td>{ownedGroupLabel(t, g.module, g.group)}</Table.Td>
                  <Table.Td>{g.count}</Table.Td>
                  <Table.Td>
                    <Select
                      aria-label={t('usersAdmin.deactivate.transferLabel', { what: ownedGroupLabel(t, g.module, g.group) })}
                      data={colleagueOptions}
                      value={choices[key(g.module, g.group)] ?? null}
                      onChange={(value) => setChoices((c) => ({ ...c, [key(g.module, g.group)]: value }))}
                      placeholder={t('usersAdmin.deactivate.choose')}
                      searchable
                    />
                  </Table.Td>
                </Table.Tr>
              ))}
            </Table.Tbody>
          </Table>
        )}

        <Textarea
          label={t('usersAdmin.deactivate.reason')}
          value={reason}
          onChange={(e) => setReason(e.currentTarget.value)}
          required
          autosize
          minRows={2}
          maxLength={500}
        />
        <Checkbox label={t('usersAdmin.deactivate.endEmployment')} checked={endEmployment} onChange={(e) => setEndEmployment(e.currentTarget.checked)} />

        <Group justify="flex-end" mt="sm">
          <Button variant="default" onClick={onClose} disabled={submitting}>
            {t('common.cancel')}
          </Button>
          <Button color="red" leftSection={<UserX size={16} />} onClick={() => void submit()} disabled={!canSubmit} loading={submitting}>
            {t('usersAdmin.deactivate.confirm')}
          </Button>
        </Group>
      </Stack>
    </Modal>
  )
}
