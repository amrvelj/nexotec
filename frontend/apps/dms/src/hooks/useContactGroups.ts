import { useMemo } from 'react'
import { useQuery } from '@tanstack/react-query'
import { useTranslation } from 'react-i18next'
import { api } from '../api/client'
import type { ContactGroupList, ContactGroupRead } from '../api/types'
import { useAuth } from '../auth/AuthContext'
import { LABEL_FIELD, resolveLanguage } from './useReferenceValueOptions'

export const contactGroupsKey = (dealershipId: string) => ['contact-groups', dealershipId] as const

/**
 * KAN-466 — the platform's contact groups as the active Betrieb sees them
 * (`GET /v1/dealerships/{id}/contact-groups`): every group, its four labels,
 * whether Nexotec keeps it active and whether this Betrieb has it on.
 * `label(code)` names any code a customer carries — switched off, withdrawn
 * or a sister Betrieb's included; `offered` are the codes a picker offers.
 */
export function useContactGroups() {
  const { i18n, t } = useTranslation()
  const { activeDealership } = useAuth()
  const dealershipId = activeDealership?.id ?? ''
  const query = useQuery({
    queryKey: contactGroupsKey(dealershipId),
    queryFn: () => api.get<ContactGroupList>(`/dealerships/${dealershipId}/contact-groups`),
    enabled: dealershipId !== '',
    staleTime: 60_000,
  })
  const field = LABEL_FIELD[resolveLanguage(i18n.language)]
  return useMemo(() => {
    const items: ContactGroupRead[] = query.data?.items ?? []
    const byCode = new Map(items.map((item) => [item.valueCode, item]))
    const label = (code: string) => {
      const item = byCode.get(code)
      return item ? item[field] : t('contactGroups.field.unknown', { code })
    }
    const offered = new Set(items.filter((item) => item.enabled).map((item) => item.valueCode))
    return { dealershipId, query, items, label, offered }
  }, [query, field, dealershipId, t])
}
