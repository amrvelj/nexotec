import { useParams } from 'react-router-dom'
import { CustomerDetailContent } from '../components/customer-detail/CustomerDetailContent'

/** The real route — reads `id` from the URL and renders as the full page. */
export function CustomerDetailPage() {
  const { id } = useParams<{ id: string }>()
  if (!id) return null
  return <CustomerDetailContent customerId={id} />
}
