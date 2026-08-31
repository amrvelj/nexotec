import { useNavigate, useSearchParams } from 'react-router-dom'
import { useQuery } from '@tanstack/react-query'
import { Loader } from '@mantine/core'
import { useSetBreadcrumb } from '@nexotec/ui-kit'
import { useTranslation } from 'react-i18next'
import { api } from '../api/client'
import { ValuationCreateDialog } from '../components/ValuationCreateDialog'
import type { ValuationRead } from '../api/types'

/** `/valuations/new` (optionally `?supersedes=<id>` for "Neu bewerten") —
 * a dedicated route rather than a modal state on the list page, so a
 * direct link/refresh lands here too, matching OfferCreateRedirectPage's
 * own routing shape for the analogous "new draft" entry point.
 */
export function ValuationCreatePage() {
  const { t } = useTranslation()
  const navigate = useNavigate()
  const [searchParams] = useSearchParams()
  const supersedesId = searchParams.get('supersedes')

  useSetBreadcrumb([t('shell.nav.valuations'), t('valuationCreate.title')])

  const supersedesQuery = useQuery({
    queryKey: ['valuation', supersedesId],
    queryFn: () => api.get<ValuationRead>(`/valuations/${supersedesId}`),
    enabled: Boolean(supersedesId),
  })

  if (supersedesId && supersedesQuery.isLoading) return <Loader />

  return (
    <ValuationCreateDialog
      opened
      onClose={() => navigate('/valuations')}
      onCreated={(valuation) => navigate(`/valuations/${valuation.id}`, { replace: true })}
      supersedes={supersedesQuery.data ?? null}
    />
  )
}
