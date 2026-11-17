import { useState } from 'react'
import { useCanWrite } from '../../../../auth/useCanWrite'
import { MultiSelect, Text } from '@mantine/core'
import type { ContactRole, CustomerUpdateInput } from '../../../../api/types'
import { FormDialog, KeyValueRow } from '@nexotec/ui-kit'
import { useTranslation } from 'react-i18next'
import { translatedRoleOptions } from '../../customerOptions'

/**
 * KAN-464 — the roles the contact holds (Kunde, Lieferant), edited inline
 * (full replace). A contact holds at least one: clearing the last one is not
 * sent. Taking the customer role away closes the customer's status and
 * advisor in the same save (the API's rule; a credit block refuses it,
 * `customer_role_credit_blocked`), so the user confirms it first.
 *
 * Q3 (Anto, 2026-11-14, A): a user who may not give the customer role
 * (`contact_customer_role`, e.g. inventory) sees it but cannot add or remove
 * it — the option and its pill are fixed; the supplier role stays theirs.
 */
export function ContactRolesField({
  label,
  value,
  onSaveField,
}: {
  label: string
  value: ContactRole[]
  onSaveField: (patch: Partial<CustomerUpdateInput>) => Promise<unknown> | void
}) {
  const { t } = useTranslation()
  const mayWriteCustomerRole = useCanWrite('contact_customer_role')
  const options = translatedRoleOptions(t).map((option) =>
    option.value === 'customer' && !mayWriteCustomerRole ? { ...option, disabled: true } : option,
  )
  const [pending, setPending] = useState<ContactRole[] | null>(null)
  const [saving, setSaving] = useState(false)

  const confirm = async () => {
    if (!pending) return
    setSaving(true)
    try {
      await onSaveField({ roles: pending })
    } finally {
      setSaving(false)
      setPending(null)
    }
  }

  return (
    <KeyValueRow label={label}>
      <MultiSelect
        size="xs"
        aria-label={label}
        data={options}
        value={value}
        onChange={(raw) => {
          const next = raw as ContactRole[]
          if (next.length === 0) return
          // The customer role stays as it is for a user who may not give it.
          if (!mayWriteCustomerRole && value.includes('customer') !== next.includes('customer')) return
          if (value.includes('customer') && !next.includes('customer')) setPending(next)
          else void onSaveField({ roles: next })
        }}
        comboboxProps={{ withinPortal: true }}
      />
      <FormDialog
        opened={pending !== null}
        onClose={() => setPending(null)}
        title={t('customerDetail.overview.removeCustomerRole.title')}
        onSubmit={confirm}
        submitLabel={t('customerDetail.overview.removeCustomerRole.confirm')}
        cancelLabel={t('common.cancel')}
        submitting={saving}
        destructive
      >
        <Text fz="sm">{t('customerDetail.overview.removeCustomerRole.body')}</Text>
      </FormDialog>
    </KeyValueRow>
  )
}
