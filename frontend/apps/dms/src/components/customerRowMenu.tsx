import { Ban, Car, Copy, FileSignature, GitMerge, Handshake, Lock, Pencil } from 'lucide-react'
import type { DetailHeaderAction, RowMenuGroups } from '@nexotec/ui-kit'
import type { CustomerRead } from '../api/types'

/**
 * KAN-44 (KAN-16's leftover, ADR-061 anti-drift) — the ONE function both
 * `CustomerDetailPage`'s header/overflow and `CustomersListPage`'s grid
 * row menu call, so a disabled action can never carry a different reason
 * (or a different enabled state) on the two surfaces. Same pattern as
 * `buildValuationRowMenu` / `buildConnectionRowMenu`.
 *
 * FR-21 / ADR-065: a `creditBlock` customer may still be QUOTED — an offer
 * commits nobody and is often how a block gets resolved — but may not be
 * CONTRACTED. `do_not_contact` stops both, because sending a document is
 * contact. FR-22 (amended 2026-09-07) is where that rule reaches the user:
 * "New contract" is disabled with the block reason named.
 */

export interface CustomerRowMenuHandlers {
  /** Present on both surfaces. */
  onEdit: () => void
  onNewOffer: () => void
  onCopyCustomerNumber: () => void
  onToggleDoNotContact: () => void
  onManageCreditBlock: () => void
  /** Detail-screen only — both open a modal that needs data (the merge
   * picker needs the customer's phones/emails), gated out of the list
   * menu the same way `buildValuationRowMenu` gates out what a list can't
   * cleanly do. */
  onMergeInto?: () => void
  onLinkVehicle?: () => void
  /**
   * There is no customer→contract flow yet (`POST /sales/contracts` takes
   * an `offerId` only; nothing attaches a customer to a bare contract).
   * "New contract" therefore always renders (FR-22 requires it on both
   * surfaces) but stays disabled with a "not yet available" reason when no
   * flag blocks it — the same posture `StockDetailPage` takes for its own
   * unbuilt create actions. Wire this once the Sales flow exists.
   */
  onNewContract?: () => void
}

export interface CustomerRowMenu {
  primary: DetailHeaderAction
  alternative: DetailHeaderAction
  overflow: RowMenuGroups
}

type T = (key: string, options?: Record<string, unknown>) => string

export function buildCustomerRowMenu(
  t: T,
  customer: Pick<CustomerRead, 'lifecycleStatus' | 'creditBlock' | 'creditBlockReason'>,
  handlers: CustomerRowMenuHandlers,
): CustomerRowMenu {
  const isDoNotContact = customer.lifecycleStatus === 'do_not_contact'
  const isBlocked = customer.creditBlock
  const isMerged = customer.lifecycleStatus === 'merged'

  const newOfferDisabledReason = isDoNotContact ? t('customerRowMenu.newOfferDisabledDoNotContact') : undefined

  // FR-22: do-not-contact wins over the block for the message, because it
  // is the stronger prohibition (it stops the offer too); then the block,
  // whose reason must be named; then the honest "flow not built" state.
  // D-20 / KAN-55: when onNewContract is finally wired (customer→contract
  // flow), add a missing-address disabledReason arm here — after the
  // block, before "not yet available". The contract-confirm refusal
  // already enforces it; this is the FR-22 proactive surface.
  const newContractDisabledReason = isDoNotContact
    ? t('customerRowMenu.newContractDisabledDoNotContact')
    : isBlocked
      ? t('customerRowMenu.newContractDisabledBlocked', { reason: customer.creditBlockReason ?? '—' })
      : t('customerRowMenu.newContractNotYetAvailable')

  const newOffer = {
    label: t('customerRowMenu.newOffer'),
    icon: <Handshake size={16} />,
    onClick: handlers.onNewOffer,
    disabled: isDoNotContact,
    disabledReason: newOfferDisabledReason,
  }

  const newContract = {
    label: t('customerRowMenu.newContract'),
    icon: <FileSignature size={16} />,
    onClick: handlers.onNewContract ?? (() => {}),
    disabled: isDoNotContact || isBlocked || !handlers.onNewContract,
    disabledReason: newContractDisabledReason,
  }

  const edit: RowMenuGroups['edit'] = [
    { label: t('customerRowMenu.edit'), icon: <Pencil size={16} />, onClick: handlers.onEdit },
    ...(handlers.onLinkVehicle
      ? [{ label: t('customerRowMenu.linkVehicle'), icon: <Car size={16} />, onClick: handlers.onLinkVehicle }]
      : []),
    {
      label: isDoNotContact ? t('customerRowMenu.removeDoNotContact') : t('customerRowMenu.setDoNotContact'),
      icon: <Ban size={16} />,
      onClick: handlers.onToggleDoNotContact,
    },
    {
      label: isBlocked ? t('customerRowMenu.removeCreditBlock') : t('customerRowMenu.setCreditBlock'),
      icon: <Lock size={16} />,
      onClick: handlers.onManageCreditBlock,
    },
  ]

  const createFrom: RowMenuGroups['createFrom'] = [
    newOffer,
    newContract,
    ...(handlers.onMergeInto
      ? [
          {
            label: t('customerRowMenu.mergeInto'),
            icon: <GitMerge size={16} />,
            onClick: handlers.onMergeInto,
            disabled: isMerged,
            disabledReason: isMerged ? t('customerRowMenu.mergeDisabledMerged') : undefined,
          },
        ]
      : []),
  ]

  const exportPrint: RowMenuGroups['exportPrint'] = [
    { label: t('customerRowMenu.copyCustomerNumber'), icon: <Copy size={16} />, onClick: handlers.onCopyCustomerNumber },
  ]

  return {
    primary: { label: t('customerRowMenu.edit'), icon: <Pencil size={16} />, onClick: handlers.onEdit },
    alternative: newOffer,
    overflow: { edit, createFrom, exportPrint },
  }
}
