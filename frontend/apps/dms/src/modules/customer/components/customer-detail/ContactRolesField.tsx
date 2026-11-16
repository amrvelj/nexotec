import { MultiSelect } from '@mantine/core'
import type { ContactRole, CustomerUpdateInput } from '../../../../api/types'
import { KeyValueRow } from '@nexotec/ui-kit'
import { useTranslation } from 'react-i18next'
import { translatedRoleOptions } from '../../customerOptions'

/**
 * KAN-464 — the roles the contact holds (Kunde, Lieferant), edited inline
 * (full replace). A contact holds at least one: clearing the last one is not
 * sent. Taking the customer role away closes the customer's status and
 * advisor in the same save (the API's rule; a credit block refuses it,
 * `customer_role_credit_blocked`).
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
  return (
    <KeyValueRow label={label}>
      <MultiSelect
        size="xs"
        aria-label={label}
        data={translatedRoleOptions(t)}
        value={value}
        onChange={(next) => {
          if (next.length > 0) void onSaveField({ roles: next as ContactRole[] })
        }}
        comboboxProps={{ withinPortal: true }}
      />
    </KeyValueRow>
  )
}
