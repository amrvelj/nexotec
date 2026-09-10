import { useNavigate } from 'react-router-dom'
import { useTranslation } from 'react-i18next'
import { useSetBreadcrumb } from '@nexotec/ui-kit'
import { CustomerCreateDialog } from '../components/CustomerCreateDialog'

/**
 * `/customers/new` — a real route so a pasted or bookmarked link and a
 * browser refresh both land somewhere, following the ValuationCreatePage
 * shape. It renders nothing of its own: the create surface is a dialog
 * (FR-05 / FR-20), and closing or cancelling returns to the list. The
 * common entry point is the "New customer" button on the list, which
 * opens the same dialog in place without navigating.
 */
export function CustomerCreatePage() {
  const { t } = useTranslation()
  const navigate = useNavigate()
  useSetBreadcrumb([t('shell.nav.masterData'), t('shell.nav.customers'), t('customerCreate.title')])

  return (
    <CustomerCreateDialog
      opened
      onClose={() => navigate('/customers')}
      onCreated={(customer) => navigate(`/customers/${customer.id}`)}
      onOpenExisting={(customerId) => navigate(`/customers/${customerId}`)}
    />
  )
}
