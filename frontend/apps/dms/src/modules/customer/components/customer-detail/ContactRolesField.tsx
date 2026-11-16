import { useState } from 'react'
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
        data={translatedRoleOptions(t)}
        value={value}
        onChange={(raw) => {
          const next = raw as ContactRole[]
          if (next.length === 0) return
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
