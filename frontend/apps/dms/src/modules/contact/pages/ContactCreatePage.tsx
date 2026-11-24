import { useNavigate } from 'react-router-dom'
import { useTranslation } from 'react-i18next'
import { useSetBreadcrumb } from '@nexotec/ui-kit'
import { ContactCreateDialog } from '../components/ContactCreateDialog'

/**
 * `/contacts/new` — a real route so a pasted or bookmarked link and a
 * browser refresh both land somewhere, following the ValuationCreatePage
 * shape. It renders nothing of its own: the create surface is a dialog
 * (FR-05 / FR-20), and closing or cancelling returns to the list. The
 * common entry point is the "New contact" button on the list, which
 * opens the same dialog in place without navigating.
 */
export function ContactCreatePage() {
  const { t } = useTranslation()
  const navigate = useNavigate()
  useSetBreadcrumb([t('shell.nav.masterData'), { label: t('shell.nav.contacts'), href: '/contacts' }, t('contactCreate.title')])

  return (
    <ContactCreateDialog
      opened
      onClose={() => navigate('/contacts')}
      onCreated={(contact) => navigate(`/contacts/${contact.id}`)}
      onOpenExisting={(contactId) => navigate(`/contacts/${contactId}`)}
    />
  )
}
