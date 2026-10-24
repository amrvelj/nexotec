import { useState } from 'react'
import { Alert, Button, Group, Stack, Table, Text } from '@mantine/core'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Landmark, Pencil, Plus, Star, Trash2 } from 'lucide-react'
import { Badge, FormDialog, RowMenu } from '@nexotec/ui-kit'
import { useTranslation } from 'react-i18next'
import { ApiError, api } from '../../api/client'
import type { BankAccountList, BankAccountRead } from '../../api/types'
import { businessRefusalText, formatIban } from '../../businessOptions'
import { BankAccountDialog } from './BankAccountDialog'
import { SectionCard } from './SectionCard'
import { bankAccountsKey } from './queryKeys'

/**
 * Business › Banking (KAN-203, D-A-27): the Betrieb's accounts as a
 * collection, one of them the invoice account, chosen explicitly (Anto,
 * 2026-10-24). The API answers 404 to a role outside manager, finance and
 * the auditor (ADR-100) — for them the section is not here at all, as if the
 * Betrieb had no accounts on file: the section renders nothing.
 */
export function BankingSection({ dealershipId }: { dealershipId: string }) {
  const { t } = useTranslation()
  const queryClient = useQueryClient()
  const accounts = useQuery({
    queryKey: bankAccountsKey(dealershipId),
    queryFn: () => api.get<BankAccountList>(`/dealerships/${dealershipId}/bank-accounts`),
    retry: false,
  })
  const [dialog, setDialog] = useState<{ account: BankAccountRead | null } | null>(null)
  const [deleting, setDeleting] = useState<BankAccountRead | null>(null)
  const [error, setError] = useState<string | null>(null)

  const refresh = () => queryClient.invalidateQueries({ queryKey: bankAccountsKey(dealershipId) })
  const makeDefault = useMutation({
    mutationFn: (a: BankAccountRead) => api.patch<BankAccountRead>(`/dealerships/${dealershipId}/bank-accounts/${a.id}`, { isDefault: true }, { 'If-Match': String(a.version) }),
    onSuccess: refresh,
    onError: (err) => setError(businessRefusalText(t, err)),
  })
  const remove = useMutation({
    mutationFn: (a: BankAccountRead) => api.delete(`/dealerships/${dealershipId}/bank-accounts/${a.id}`, { 'If-Match': String(a.version) }),
    onSuccess: async () => {
      setDeleting(null)
      await refresh()
    },
    onError: (err) => setError(businessRefusalText(t, err)),
  })

  // ADR-100: a 404 means "not for this role" — nothing to show, not an error.
  if (accounts.error instanceof ApiError && accounts.error.status === 404) return null
  if (accounts.isLoading) return null

  const rows = accounts.data?.items ?? []
  const hasDefault = rows.some((a) => a.isDefault)

  return (
    <SectionCard title={t('business.banking.title')} icon={<Landmark size={20} />} error={error} onDismissError={() => setError(null)}>
      <Stack gap="md">
        {accounts.isError && <Alert color="red" role="alert">{t('business.loadError')}</Alert>}
        {!accounts.isError && rows.length === 0 && (
          <Text c="dimmed" fz="sm">
            {t('business.banking.noneYet')}
          </Text>
        )}
        {rows.length > 0 && !hasDefault && (
          <Alert color="yellow" withCloseButton={false}>
            {t('business.banking.noDefault')}
          </Alert>
        )}
        {rows.length > 0 && (
          <Table>
            <Table.Thead>
              <Table.Tr>
                <Table.Th>{t('business.banking.columns.label')}</Table.Th>
                <Table.Th>{t('business.banking.columns.holder')}</Table.Th>
                <Table.Th>{t('business.banking.columns.bank')}</Table.Th>
                <Table.Th>{t('business.banking.columns.iban')}</Table.Th>
                <Table.Th>{t('business.banking.columns.qr')}</Table.Th>
                <Table.Th />
              </Table.Tr>
            </Table.Thead>
            <Table.Tbody>
              {rows.map((a) => (
                <Table.Tr key={a.id}>
                  <Table.Td>
                    <Group gap="xs">
                      <Text fz="sm">{a.label ?? '—'}</Text>
                      {a.isDefault && <Badge tone="purple">{t('business.banking.default')}</Badge>}
                    </Group>
                  </Table.Td>
                  <Table.Td>{a.accountHolder}</Table.Td>
                  <Table.Td>{a.bankName}</Table.Td>
                  <Table.Td>
                    <Text fz="sm" ff="monospace">
                      {formatIban(a.iban)}
                    </Text>
                  </Table.Td>
                  <Table.Td>
                    <Text fz="sm">{t(`business.banking.qrTypes.${a.qrReferenceType}`)}</Text>
                    {a.qrIban && (
                      <Text fz="xs" c="dimmed" ff="monospace">
                        {formatIban(a.qrIban)}
                      </Text>
                    )}
                  </Table.Td>
                  <Table.Td>
                    <RowMenu
                      ariaLabel={t('common.rowActionsLabel')}
                      groups={{
                        edit: [
                          { label: t('business.banking.edit'), icon: <Pencil size={16} />, onClick: () => setDialog({ account: a }) },
                          {
                            label: t('business.banking.makeDefault'),
                            icon: <Star size={16} />,
                            onClick: () => makeDefault.mutate(a),
                            disabled: a.isDefault,
                            disabledReason: a.isDefault ? t('business.banking.isAlreadyDefault') : undefined,
                          },
                        ],
                        destructive: [{ label: t('business.banking.delete'), icon: <Trash2 size={16} />, onClick: () => setDeleting(a) }],
                      }}
                    />
                  </Table.Td>
                </Table.Tr>
              ))}
            </Table.Tbody>
          </Table>
        )}
        <Group>
          <Button variant="default" leftSection={<Plus size={16} />} onClick={() => setDialog({ account: null })}>
            {t('business.banking.add')}
          </Button>
        </Group>
      </Stack>
      <BankAccountDialog opened={dialog !== null} onClose={() => setDialog(null)} dealershipId={dealershipId} account={dialog?.account ?? null} />
      <FormDialog
        opened={deleting !== null}
        onClose={() => setDeleting(null)}
        title={t('business.banking.delete')}
        onSubmit={() => {
          if (deleting) remove.mutate(deleting)
        }}
        submitLabel={t('business.banking.delete')}
        cancelLabel={t('common.cancel')}
        submitting={remove.isPending}
        destructive
      >
        <Text>{deleting ? t('business.banking.deleteConfirm', { iban: formatIban(deleting.iban) }) : ''}</Text>
      </FormDialog>
    </SectionCard>
  )
}
