import { useQuery } from '@tanstack/react-query'
import { api } from '../api/client'
import type { StatusPageRead } from '../api/types'
import { BACKGROUND_REQUEST_HEADERS } from './useNotifications'

/** The Betrieb's status page (KAN-223; D-A-38). The page and the shell's
 * indicator share one query; the indicator asks again every five minutes as
 * a background request (it never keeps an unattended tab alive). */
export const statusPageKey = (dealershipId: string) => ['status-page', dealershipId] as const

const INDICATOR_POLL_MS = 5 * 60_000

export function useStatusPage(dealershipId: string, enabled: boolean, { poll = false }: { poll?: boolean } = {}) {
  return useQuery({
    queryKey: statusPageKey(dealershipId),
    queryFn: () =>
      api.get<StatusPageRead>(`/dealerships/${dealershipId}/status`, poll ? BACKGROUND_REQUEST_HEADERS : undefined),
    enabled: enabled && dealershipId !== '',
    refetchInterval: enabled && poll ? INDICATOR_POLL_MS : false,
  })
}
