import { useParams } from 'react-router-dom'
import { StockDetailContent } from '../components/stock-detail/StockDetailContent'

/** The real route — reads `id` from the URL and renders as the full page. */
export function StockDetailPage() {
  const { id } = useParams<{ id: string }>()
  if (!id) return null
  return <StockDetailContent stockItemId={id} />
}
