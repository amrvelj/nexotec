import type { ContactRead } from '../../../../api/types'
import { ContactBlockDialog } from './ContactBlockDialog'

interface CreditBlockDialogProps {
  opened: boolean
  onClose: () => void
  contact: Pick<ContactRead, 'id' | 'version' | 'contactNumber' | 'creditBlock' | 'blocked'>
  onSaved: (updated: ContactRead) => void
}

/** KAN-44 / ADR-065 (S-D19): the credit block's dialog — `ContactBlockDialog` of kind `credit`. */
export function CreditBlockDialog(props: CreditBlockDialogProps) {
  return <ContactBlockDialog kind="credit" {...props} />
}
