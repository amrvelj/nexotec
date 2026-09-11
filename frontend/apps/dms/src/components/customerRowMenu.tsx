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
   * KAN-58 — `POST /sales/contracts` now accepts `customerId` directly, so
   * this is wired on both surfaces (list row menu and 360 header/overflow),
   * same as `onNewOffer` above.
   */
  onNewContract: () => void
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
  // whose reason must be named. D-20 / KAN-55's missing-address gate stays
  // confirm-time only (never re-litigated at create) — see contract.py.
  const newContractDisabledReason = isDoNotContact
    ? t('customerRowMenu.newContractDisabledDoNotContact')
    : isBlocked
      ? t('customerRowMenu.newContractDisabledBlocked', { reason: customer.creditBlockReason ?? '—' })
      : undefined

  const newOffer = {
    label: t('customerRowMenu.newOffer'),
    icon: <Handshake size={16} />,
    onClick: handlers.onNewOffer,
    disabled: isDoNotContact,
    disabledReason: newOfferDisabledReason,
  }

  // Deliberately stricter than the backend: create_contract's own customerId
  // path (KAN-58) actually PERMITS creating a pending contract for a
  // credit-blocked customer — only confirm refuses it (ADR-065). This
  // proactive disable is KAN-44's original FR-22 policy, unchanged by
  // KAN-58 (see the ticket's own exit criterion 3): don't let the advisor
  // start a contract that finance hasn't released yet, even though nothing
  // downstream would reject the create call itself.
  const newContract = {
    label: t('customerRowMenu.newContract'),
    icon: <FileSignature size={16} />,
    onClick: handlers.onNewContract,
    disabled: isDoNotContact || isBlocked,
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
