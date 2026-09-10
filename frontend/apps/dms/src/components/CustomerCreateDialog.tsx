import { Modal } from '@mantine/core'
import { useTranslation } from 'react-i18next'
import { CustomerCreateFlow } from './CustomerCreateFlow'
import type { CustomerRead, CustomerType } from '../api/types'

export interface CustomerCreateDialogProps {
  opened: boolean
  onClose: () => void
  onCreated: (customer: CustomerRead) => void
  /** FR-04 / FR-20: "open this customer instead" from the live duplicate
   * panel. Omit where there is nowhere sensible to navigate to. */
  onOpenExisting?: (customerId: string) => void
  initialCustomerType?: CustomerType
}

/**
 * FR-05 / FR-20 / UI/UX §Forms: "Creating a record uses a DIALOG … Sales
 * opens THIS dialog, not a thinner copy of it." The one create-customer
 * surface — a plain Mantine Modal wrapping {@link CustomerCreateFlow},
 * whose ui-kit `Wizard` already owns the numbered step header, the footer
 * (Cancel / Back / Next / Create) and the error slot. ui-kit `FormDialog`
 * is deliberately not used here: it renders its own submit/cancel footer,
 * which would double the Wizard's and cannot express step 1's "Next"
 * (advance) versus step 2's "Create customer" (submit).
 *
 * `#/customers` opens this over the list; `/customers/new` renders it via
 * CustomerCreatePage for a pasted or bookmarked link; Sales' offer
 * workspace and the valuation create dialog mount the same component.
 */
export function CustomerCreateDialog({ opened, onClose, onCreated, onOpenExisting, initialCustomerType }: CustomerCreateDialogProps) {
  const { t } = useTranslation()

  return (
    <Modal opened={opened} onClose={onClose} title={t('customerCreate.title')} size="lg">
      <CustomerCreateFlow
        onSuccess={onCreated}
        onCancel={onClose}
        onOpenExisting={onOpenExisting}
        initialCustomerType={initialCustomerType}
      />
    </Modal>
  )
}
