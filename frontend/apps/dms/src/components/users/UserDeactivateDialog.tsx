import { useEffect, useMemo, useState } from 'react'
import { Alert, Button, Checkbox, Select, Stack, Table, Text, Textarea } from '@mantine/core'
import { useQuery } from '@tanstack/react-query'
import { FormDialog } from '@nexotec/ui-kit'
import { useTranslation } from 'react-i18next'
import { api, ApiError } from '../../api/client'
import type {
  OwnershipRead,
  OwnershipTransferRequest,
  OwnershipTransfersRead,
  OwnershipTransfersRequest,
  UserDeactivateRequest,
  UserDeactivatedRead,
  UserAdminRead,
} from '../../api/types'
import { useIdempotencyKey } from '../../hooks/useIdempotencyKey'
import { ownedGroupLabel, reassignmentProblemText, refusalText, userDisplayName } from '../../userOptions'
import { formatNumber } from '../../utils/format'

export interface UserDeactivateDialogProps {
  opened: boolean
  onClose: () => void
  dealershipId: string
  user: UserAdminRead | null
  /** The Betrieb's people; only active colleagues can receive records (FR-A-37). */
  colleagues: UserAdminRead[]
  /**
   * `deactivate` (the default): the deactivation with its reassignment step.
   * `transfer`: the hand-over alone — the retry after a module failed during
   * a deactivation, or a plain hand-over for a user who stays.
   */
  mode?: 'deactivate' | 'transfer'
  onDone: (result: UserDeactivatedRead | OwnershipTransfersRead) => void
}

const UNASSIGNED = '__unassigned__'

/**
 * KAN-200 — deactivation with the reassignment step (FR-A-37, D-A-35).
 * What the person still owns is listed by module; the manager hands each
 * group to a colleague or leaves it unassigned (the module's own queue:
 * Sales' "Nicht zugewiesen" chip, Customers' advisor filter). Every group
 * needs a decision before the button works — nothing is left silently with
 * the leaver. The kit's form dialog in its destructive tone, as the UI spec
 * reserves for an irreversible action; there is no delete (A-RULE-5).
 */
export function UserDeactivateDialog({ opened, onClose, dealershipId, user, colleagues, mode = 'deactivate', onDone }: UserDeactivateDialogProps) {
  const { t } = useTranslation()
  const deactivating = mode === 'deactivate'
  const [reason, setReason] = useState('')
  const [endEmployment, setEndEmployment] = useState(true)
  const [choices, setChoices] = useState<Record<string, string | null>>({})
  const [submitting, setSubmitting] = useState(false)
  const [error, setError] = useState<string | null>(null)
  // The 422's `details.problems`: which group, and why — each a stable
  // `reason` the bundles translate (an unknown one falls back to the API's
  // English `message`, KAN-398's open rule), never only the generic line.
  const [problems, setProblems] = useState<string[]>([])
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
    setProblems([])
    idempotency.renew()
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [opened, user?.id, mode])

  const colleagueOptions = useMemo(
    () => [
      { value: UNASSIGNED, label: t('usersAdmin.deactivate.leaveUnassigned') },
      ...colleagues.filter((c) => c.id !== user?.id && c.status === 'active').map((c) => ({ value: c.id, label: userDisplayName(c) })),
    ],
    [colleagues, user?.id, t],
  )

  const groups = ownership.data?.groups ?? []
  const key = (module: string, group: string) => `${module}/${group}`
  const undecided = groups.filter((g) => choices[key(g.module, g.group)] === undefined)
  const canSubmit =
    (deactivating ? reason.trim().length > 0 : groups.length > 0) && undecided.length === 0 && ownership.isSuccess && !submitting

  const submit = async () => {
    if (!user) return
    setSubmitting(true)
    setError(null)
    setProblems([])
    const transfers: OwnershipTransferRequest[] = groups.map((g) => {
      const choice = choices[key(g.module, g.group)]
      return { module: g.module, group: g.group, toUserId: choice === UNASSIGNED || !choice ? null : choice }
    })
    try {
      if (deactivating) {
        const body: UserDeactivateRequest = {
          reason: reason.trim(),
          transfers,
          ...(endEmployment ? { employmentStatus: 'terminated' as const } : {}),
        }
        const result = await api.post<UserDeactivatedRead>(`/dealerships/${dealershipId}/users/${user.id}/deactivate`, body, {
          ...idempotency.headers(body),
          'If-Match': String(user.version),
        })
        idempotency.renew()
        onDone(result)
      } else {
        const body: OwnershipTransfersRequest = { transfers, reason: reason.trim() || null }
        const result = await api.post<OwnershipTransfersRead>(
          `/dealerships/${dealershipId}/users/${user.id}/ownership/transfers`,
          body,
          idempotency.headers(body),
        )
        idempotency.renew()
        onDone(result)
      }
    } catch (err) {
      if (err instanceof ApiError && err.status === 400) setError(refusalText(t, err)) // the last-manager rule, in the UI language
      else if (err instanceof ApiError && err.status === 409) setError(t('usersAdmin.form.conflict'))
      else if (err instanceof ApiError && err.status === 422 && Array.isArray(err.details?.problems)) {
        setError(t('usersAdmin.deactivate.incomplete'))
        setProblems((err.details.problems as unknown[]).map((entry) => reassignmentProblemText(t, entry)))
      } else if (err instanceof ApiError) setError(err.message)
      else setError(t('common.requestFailed'))
      void ownership.refetch()
    } finally {
      setSubmitting(false)
    }
  }

  const name = user ? userDisplayName(user) : ''
  return (
    <FormDialog
      opened={opened}
      onClose={onClose}
      title={deactivating ? t('usersAdmin.deactivate.title', { name }) : t('usersAdmin.handOver.title', { name })}
      size="lg"
      destructive={deactivating}
      onSubmit={submit}
      submitLabel={deactivating ? t('usersAdmin.deactivate.confirm') : t('usersAdmin.handOver.confirm')}
      cancelLabel={t('common.cancel')}
      submitting={submitting}
      submitDisabled={!canSubmit}
    >
      <Stack gap="md">
        <Text size="sm">{deactivating ? t('usersAdmin.deactivate.intro') : t('usersAdmin.handOver.intro')}</Text>
        {error && (
          <Alert color="red" role="alert">
            {error}
            {problems.length > 0 && (
              <ul style={{ margin: '8px 0 0', paddingLeft: 18 }}>
                {problems.map((problem) => (
                  <li key={problem}>{problem}</li>
                ))}
              </ul>
            )}
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
                  <Table.Td>{formatNumber(g.count)}</Table.Td>
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
          required={deactivating}
          autosize
          minRows={2}
          maxLength={500}
        />
        {deactivating && (
          <Checkbox label={t('usersAdmin.deactivate.endEmployment')} checked={endEmployment} onChange={(e) => setEndEmployment(e.currentTarget.checked)} />
        )}
      </Stack>
    </FormDialog>
  )
}
