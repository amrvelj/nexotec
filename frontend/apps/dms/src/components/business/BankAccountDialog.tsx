import { useEffect, useState } from 'react'
import { Checkbox, Select, SimpleGrid, Stack, TextInput } from '@mantine/core'
import { useMutation, useQueryClient } from '@tanstack/react-query'
import { fieldHint, FormDialog } from '@nexotec/ui-kit'
import { useTranslation } from 'react-i18next'
import { api } from '../../api/client'
import type { BankAccountCreate, BankAccountRead, BankAccountUpdate, QrReferenceType } from '../../api/types'
import { QR_REFERENCE_TYPES, bankingWriteRefusalText, orNull } from '../../businessOptions'
import { useIdempotencyKey } from '../../hooks/useIdempotencyKey'
import { bankAccountsKey } from './queryKeys'

export interface BankAccountDialogProps {
  opened: boolean
  onClose: () => void
  dealershipId: string
  /** `null` adds an account; a record edits it. */
  account: BankAccountRead | null
}

interface Form {
  accountHolder: string
  bankName: string
  iban: string
  bic: string
  qrIban: string
  qrReferenceType: QrReferenceType
  creditorReferencePrefix: string
  label: string
  isDefault: boolean
}

const EMPTY: Form = { accountHolder: '', bankName: '', iban: '', bic: '', qrIban: '', qrReferenceType: 'non', creditorReferencePrefix: '', label: '', isDefault: false }

const toForm = (a: BankAccountRead): Form => ({
  accountHolder: a.accountHolder,
  bankName: a.bankName,
  iban: a.iban,
  bic: a.bic ?? '',
  qrIban: a.qrIban ?? '',
  qrReferenceType: a.qrReferenceType,
  creditorReferencePrefix: a.creditorReferencePrefix ?? '',
  label: a.label ?? '',
  isDefault: a.isDefault,
})

function createBody(f: Form): BankAccountCreate {
  return {
    accountHolder: f.accountHolder.trim(),
    bankName: f.bankName.trim(),
    iban: f.iban.trim(),
    bic: orNull(f.bic),
    qrIban: orNull(f.qrIban),
    qrReferenceType: f.qrReferenceType,
    creditorReferencePrefix: orNull(f.creditorReferencePrefix),
    label: orNull(f.label),
    isDefault: f.isDefault,
  }
}

function patchBody(before: BankAccountRead, f: Form): BankAccountUpdate {
  const c: BankAccountUpdate = {}
  if (f.accountHolder.trim() !== before.accountHolder) c.accountHolder = f.accountHolder.trim()
  if (f.bankName.trim() !== before.bankName) c.bankName = f.bankName.trim()
  if (f.iban.replace(/\s/g, '').toUpperCase() !== before.iban) c.iban = f.iban.trim()
  if (orNull(f.bic) !== (before.bic ?? null)) c.bic = orNull(f.bic)
  if ((orNull(f.qrIban)?.replace(/\s/g, '').toUpperCase() ?? null) !== (before.qrIban ?? null)) c.qrIban = orNull(f.qrIban)
  if (f.qrReferenceType !== before.qrReferenceType) c.qrReferenceType = f.qrReferenceType
  if (orNull(f.creditorReferencePrefix) !== (before.creditorReferencePrefix ?? null)) c.creditorReferencePrefix = orNull(f.creditorReferencePrefix)
  if (orNull(f.label) !== (before.label ?? null)) c.label = orNull(f.label)
  if (f.isDefault !== before.isDefault) c.isDefault = f.isDefault
  return c
}

const FORM_ID = 'bank-account-form'

/**
 * Add or edit one bank account (KAN-203, D-A-27). The IBAN is checked on
 * save by the API (mod-97; a QR-IBAN is told apart by its IID); the dialog
 * says the refusal in the dealer's words.
 */
export function BankAccountDialog({ opened, onClose, dealershipId, account }: BankAccountDialogProps) {
  const { t } = useTranslation()
  const queryClient = useQueryClient()
  const idempotency = useIdempotencyKey()
  const [form, setForm] = useState<Form>(EMPTY)
  const [error, setError] = useState<string | null>(null)
  useEffect(() => {
    if (opened) {
      setForm(account ? toForm(account) : EMPTY)
      setError(null)
      idempotency.renew()
    }
  }, [opened, account, idempotency])
  const set = <K extends keyof Form>(key: K, value: Form[K]) => setForm((f) => ({ ...f, [key]: value }))

  const save = useMutation({
    mutationFn: async () => {
      if (account) {
        return api.patch<BankAccountRead>(`/dealerships/${dealershipId}/bank-accounts/${account.id}`, patchBody(account, form), { 'If-Match': String(account.version) })
      }
      const body = createBody(form)
      return api.post<BankAccountRead>(`/dealerships/${dealershipId}/bank-accounts`, body, idempotency.headers(body))
    },
    onSuccess: async () => {
      await queryClient.invalidateQueries({ queryKey: bankAccountsKey(dealershipId) })
      onClose()
    },
    onError: (err) => setError(bankingWriteRefusalText(t, err)),
  })

  const qrr = form.qrReferenceType === 'qrr'
  const scor = form.qrReferenceType === 'scor'

  return (
    <FormDialog
      opened={opened}
      onClose={onClose}
      title={account ? t('business.banking.edit') : t('business.banking.add')}
      formId={FORM_ID}
      onSubmit={() => save.mutate()}
      submitLabel={t('common.save')}
      cancelLabel={t('common.cancel')}
      submitting={save.isPending}
      size="lg"
    >
      <form
        id={FORM_ID}
        onSubmit={(e) => {
          e.preventDefault()
          save.mutate()
        }}
      >
        <Stack gap="md">
          {error && (
            <div role="alert" style={{ fontSize: 14 }}>
              {error}
            </div>
          )}
          <SimpleGrid cols={{ base: 1, sm: 2 }}>
            <TextInput label={t('business.banking.fields.accountHolder')} value={form.accountHolder} onChange={(e) => set('accountHolder', e.currentTarget.value)} required />
            <TextInput label={t('business.banking.fields.bankName')} value={form.bankName} onChange={(e) => set('bankName', e.currentTarget.value)} required />
            <TextInput {...fieldHint(t('business.banking.fields.iban'), t('business.banking.fields.ibanHint'))} value={form.iban} onChange={(e) => set('iban', e.currentTarget.value)} placeholder="CH93 0076 2011 6238 5295 7" required />
            <TextInput label={t('business.banking.fields.bic')} value={form.bic} onChange={(e) => set('bic', e.currentTarget.value.toUpperCase())} placeholder="ZKBKCHZZ80A" />
            <Select {...fieldHint(t('business.banking.fields.qrReferenceType'), t('business.banking.fields.qrReferenceTypeHint'))}
              data={QR_REFERENCE_TYPES.map((v) => ({ value: v, label: t(`business.banking.qrTypes.${v}`) }))}
              value={form.qrReferenceType}
              onChange={(v) => {
                if (!v) return
                const type = v as QrReferenceType
                // What the new type does not take is cleared, so a hidden
                // field never holds a value the API would then refuse.
                setForm((f) => ({ ...f, qrReferenceType: type, qrIban: type === 'qrr' ? f.qrIban : '', creditorReferencePrefix: type === 'scor' ? f.creditorReferencePrefix : '' }))
              }}
              allowDeselect={false}
            />
            {qrr && <TextInput {...fieldHint(t('business.banking.fields.qrIban'), t('business.banking.fields.qrIbanHint'))} value={form.qrIban} onChange={(e) => set('qrIban', e.currentTarget.value)} placeholder="CH44 3199 9123 0008 8901 2" required />}
            {scor && <TextInput {...fieldHint(t('business.banking.fields.creditorReferencePrefix'), t('business.banking.fields.creditorReferencePrefixHint'))} value={form.creditorReferencePrefix} onChange={(e) => set('creditorReferencePrefix', e.currentTarget.value)} />}
            <TextInput label={t('business.banking.fields.label')} value={form.label} onChange={(e) => set('label', e.currentTarget.value)} placeholder={t('business.banking.fields.labelPlaceholder')} />
          </SimpleGrid>
          <Checkbox {...fieldHint(t('business.banking.fields.isDefault'), t('business.banking.fields.isDefaultHint'))} checked={form.isDefault} onChange={(e) => set('isDefault', e.currentTarget.checked)} />
        </Stack>
      </form>
    </FormDialog>
  )
}
