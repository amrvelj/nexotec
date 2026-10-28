import { useState } from 'react'
import { useQueryClient } from '@tanstack/react-query'
import { useTranslation } from 'react-i18next'
import { UserCheck } from 'lucide-react'
import type { RowMenuAction } from '@nexotec/ui-kit'
import { api } from '../api/client'
import { useAuth } from '../auth/AuthContext'
import type { SalesContractRead, SalesOfferRead } from '../api/types'
import { apiRefusalText } from '../utils/apiRefusal'

/** The statuses a deal is still worked on in: a draft or sent offer, a
 * pending contract (the unassigned queue's own definition, KAN-200). */
const OPEN_DEAL_STATUSES = new Set(['draft', 'open', 'pending'])

export interface TakeOverTarget {
  entityType: 'offer' | 'contract'
  status: string
  ownerUserId?: string | null
  ownerLabel?: string | null
}

/**
 * KAN-405 (ADR-061) — «Übernehmen», the ONE definition the Sales list's row
 * menu and the offer and contract detail overflows render, so its enabled
 * state and its reason never differ between list and detail. It is offered
 * on a deal nobody is responsible for; a deal with a seller is handed on
 * through the seller picker, never taken by a click from the list.
 * Anto's ruling 2026-10-28 (option A): an offer already sent to the
 * customer may change its seller, so every row of the queue can be taken.
 */
export function buildDealTakeOverAction(
  t: (key: string, options?: Record<string, unknown>) => string,
  deal: TakeOverTarget,
  currentUserId: string | null,
  onTakeOver: () => void
): RowMenuAction {
  const disabledReason = !OPEN_DEAL_STATUSES.has(deal.status)
    ? t('dealOwner.takeOverDisabled.closed')
    : deal.ownerUserId != null && deal.ownerUserId === currentUserId
      ? t('dealOwner.takeOverDisabled.yours')
      : deal.ownerUserId != null
        ? t('dealOwner.takeOverDisabled.assigned', { owner: deal.ownerLabel ?? '—' })
        : undefined
  return {
    label: t('dealOwner.takeOver'),
    icon: <UserCheck size={16} />,
    onClick: onTakeOver,
    disabled: disabledReason !== undefined,
    disabledReason,
  }
}

/** Takes a deal over for the signed-in user from a screen that holds no
 * save queue for it (the Sales list, a sent offer), PATCHing `ownerUserId`
 * alone with If-Match. The list's row carries no version, so the entity is
 * read first — and taken only if it is still unassigned there: a colleague
 * who took it since the list loaded is said by name, never overwritten
 * (the fresh version would let the PATCH through). A screen that holds the
 * entity passes it as `known`, so its own version guards the write (a
 * stale one is a 409). A refusal is said in the UI language. */
export function useTakeOverDeal() {
  const { t } = useTranslation()
  const { user } = useAuth()
  const queryClient = useQueryClient()
  const [error, setError] = useState<string | null>(null)
  const [pending, setPending] = useState(false)

  const takeOver = async (
    entityType: 'offer' | 'contract',
    id: string,
    known?: Pick<SalesOfferRead | SalesContractRead, 'version' | 'ownerUserId' | 'ownerLabel'>
  ) => {
    if (!user) return
    setPending(true)
    setError(null)
    try {
      const path = entityType === 'contract' ? `/sales/contracts/${id}` : `/sales/offers/${id}`
      const current = known ?? (await api.get<SalesOfferRead | SalesContractRead>(path))
      if (current.ownerUserId != null) {
        if (current.ownerUserId !== user.id) {
          setError(t('dealOwner.errors.takenMeanwhile', { owner: current.ownerLabel ?? '—' }))
        }
        await queryClient.invalidateQueries({ queryKey: ['sales-deals'] })
        return
      }
      const updated = await api.patch<SalesOfferRead | SalesContractRead>(
        path,
        { ownerUserId: user.id },
        { 'If-Match': String(current.version) }
      )
      queryClient.setQueryData([entityType === 'contract' ? 'sales-contract' : 'sales-offer', id], updated)
      await queryClient.invalidateQueries({ queryKey: ['sales-deals'] })
    } catch (err) {
      setError(apiRefusalText(t, err, 'dealOwner.errors.takeOverFailed'))
    } finally {
      setPending(false)
    }
  }

  return { takeOver, error, clearError: () => setError(null), pending, currentUserId: user?.id ?? null }
}
