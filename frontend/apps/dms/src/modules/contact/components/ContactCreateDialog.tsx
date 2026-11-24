import { Modal } from '@mantine/core'
import { useTranslation } from 'react-i18next'
import { ContactCreateFlow } from './ContactCreateFlow'
import type { ContactRole, ContactRead, ContactType } from '../../../api/types'

export interface ContactCreateDialogProps {
  opened: boolean
  onClose: () => void
  onCreated: (contact: ContactRead) => void
  /** FR-04 / FR-20: "open this contact instead" from the live duplicate
   * panel. Omit where there is nowhere sensible to navigate to. */
  onOpenExisting?: (contactId: string) => void
  initialContactType?: ContactType
  /** KAN-465: the roles the new contact starts with (the purchase dialog presets the supplier role). */
  initialRoles?: ContactRole[]
}

/**
 * FR-05 / FR-20 / UI/UX §Forms: "Creating a record uses a DIALOG … Sales
 * opens THIS dialog, not a thinner copy of it." The one create-contact
 * surface — a plain Mantine Modal wrapping {@link ContactCreateFlow},
 * whose ui-kit `Wizard` already owns the numbered step header, the footer
 * (Cancel / Back / Next / Create) and the error slot. ui-kit `FormDialog`
 * is deliberately not used here: it renders its own submit/cancel footer,
 * which would double the Wizard's and cannot express step 1's "Next"
 * (advance) versus step 2's "Create contact" (submit).
 *
 * `#/contacts` opens this over the list; `/contacts/new` renders it via
 * ContactCreatePage for a pasted or bookmarked link; Sales' offer
 * workspace and the valuation create dialog mount the same component.
 */
export function ContactCreateDialog({ opened, onClose, onCreated, onOpenExisting, initialContactType, initialRoles }: ContactCreateDialogProps) {
  const { t } = useTranslation()

  return (
    <Modal opened={opened} onClose={onClose} title={t('contactCreate.title')} size="lg">
      <ContactCreateFlow
        onSuccess={onCreated}
        onCancel={onClose}
        onOpenExisting={onOpenExisting}
        initialContactType={initialContactType}
        initialRoles={initialRoles}
      />
    </Modal>
  )
}
