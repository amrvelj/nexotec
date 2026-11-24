import { useParams } from 'react-router-dom'
import { ContactDetailContent } from '../components/contact-detail/ContactDetailContent'

/** The real route — reads `id` from the URL and renders as the full page. */
export function ContactDetailPage() {
  const { id } = useParams<{ id: string }>()
  if (!id) return null
  return <ContactDetailContent contactId={id} />
}
