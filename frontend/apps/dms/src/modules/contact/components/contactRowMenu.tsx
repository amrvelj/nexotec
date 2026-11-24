import { Ban, Car, Copy, FileSignature, GitMerge, Handshake, Lock } from 'lucide-react'
import type { DetailHeaderAction, RowMenuGroups } from '@nexotec/ui-kit'
import type { ContactRead } from '../../../api/types'

/**
 * KAN-44 (KAN-16's leftover, ADR-061 anti-drift) — the ONE function both
 * `ContactDetailPage`'s header/overflow and `ContactsListPage`'s grid
 * row menu call, so a disabled action can never carry a different reason
 * (or a different enabled state) on the two surfaces. Same pattern as
 * `buildValuationRowMenu` / `buildConnectionRowMenu`.
 *
 * FR-21 / ADR-065: a `creditBlock` contact may still be QUOTED — an offer
 * commits nobody and is often how a block gets resolved — but may not be
 * CONTRACTED. `do_not_contact` stops both, because sending a document is
 * contact. FR-22 (amended 2026-09-07) is where that rule reaches the user:
 * "New contract" is disabled with the block reason named.
 *
 * KAN-468 (Anto, 2026-10-26; FR-22 amended): there is no «Edit» anywhere —
 * every field on the contact is edited inline, so the old primary only
 * selected the Overview tab and did nothing there. The primary is «New
 * offer», the alternative «Link vehicle»; the list's row menu opens the
 * record with its own «Open».
 */

export interface ContactRowMenuHandlers {
  /** Present on both surfaces. */
  onCopyContactNumber: () => void
  onToggleDoNotContact: () => void
  onManageCreditBlock: () => void
  /** Detail-screen only — both open a modal that needs data (the merge
   * picker needs the contact's phones/emails), gated out of the list
   * menu the same way `buildValuationRowMenu` gates out what a list can't
   * cleanly do. */
  onMergeInto?: () => void
  onLinkVehicle?: () => void
  /**
   * KAN-58 — `POST /sales/contracts` now accepts `contactId` directly, so
   * "New contract" is wired on both surfaces (list row menu and 360
   * header/overflow), the same as "New offer".
   *
   * KAN-377 (D-A-04): both are passed only when the active Betrieb holds the
   * `sales` entitlement. A workshop's API answers 404 on `/sales/*`, so the
   * two actions are left out rather than offered and refused — presentation
   * only, the 404 stays the boundary.
   */
  onNewOffer?: () => void
  onNewContract?: () => void
}

export interface ContactRowMenu {
  /** "New offer"; for a Betrieb without sales (KAN-377), "Link vehicle"
   * where the surface offers it, else none (KAN-468). */
  primary?: DetailHeaderAction
  /** "Link vehicle" beside a "New offer" primary, else none. */
  alternative?: DetailHeaderAction
  overflow: RowMenuGroups
}

type T = (key: string, options?: Record<string, unknown>) => string

export function buildContactRowMenu(
  t: T,
  contact: Pick<ContactRead, 'lifecycleStatus' | 'creditBlock' | 'creditBlockReason'> & Partial<Pick<ContactRead, 'roles'>>,
  handlers: ContactRowMenuHandlers,
  // KAN-464 Q3: whether the user may write the customer role's data
  // (`contact_customer_role`). Without it, the credit block and the merge of
  // a contact are not offered — the server refuses both (403).
  { mayWriteCustomerRole = true }: { mayWriteCustomerRole?: boolean } = {},
): ContactRowMenu {
  const isDoNotContact = contact.lifecycleStatus === 'do_not_contact'
  const isBlocked = contact.creditBlock
  // KAN-464 (Q1): a credit block is the customer role's — a contact without
  // it is offered none (a block already standing can always be lifted).
  const holdsCustomer = contact.roles === undefined || contact.roles.includes('customer')
  const canBeBlocked = mayWriteCustomerRole && (isBlocked || holdsCustomer)
  const canBeMerged = mayWriteCustomerRole || !holdsCustomer
  const isMerged = contact.lifecycleStatus === 'merged'

  const newOfferDisabledReason = isDoNotContact ? t('contactRowMenu.newOfferDisabledDoNotContact') : undefined

  // FR-22: do-not-contact wins over the block for the message, because it
  // is the stronger prohibition (it stops the offer too); then the block,
  // whose reason must be named. D-20 / KAN-55's missing-address gate stays
  // confirm-time only (never re-litigated at create) — see contract.py.
  const newContractDisabledReason = isDoNotContact
    ? t('contactRowMenu.newContractDisabledDoNotContact')
    : isBlocked
      ? t('contactRowMenu.newContractDisabledBlocked', { reason: contact.creditBlockReason ?? '—' })
      : undefined

  const newOffer = handlers.onNewOffer && {
    label: t('contactRowMenu.newOffer'),
    icon: <Handshake size={16} />,
    onClick: handlers.onNewOffer,
    disabled: isDoNotContact,
    disabledReason: newOfferDisabledReason,
  }

  // Deliberately stricter than the backend: create_contract's own contactId
  // path (KAN-58) actually PERMITS creating a pending contract for a
  // credit-blocked contact — only confirm refuses it (ADR-065). This
  // proactive disable is KAN-44's original FR-22 policy, unchanged by
  // KAN-58 (see the ticket's own exit criterion 3): don't let the advisor
  // start a contract that finance hasn't released yet, even though nothing
  // downstream would reject the create call itself.
  const newContract = handlers.onNewContract && {
    label: t('contactRowMenu.newContract'),
    icon: <FileSignature size={16} />,
    onClick: handlers.onNewContract,
    disabled: isDoNotContact || isBlocked,
    disabledReason: newContractDisabledReason,
  }

  const linkVehicle = handlers.onLinkVehicle && {
    label: t('contactRowMenu.linkVehicle'),
    icon: <Car size={16} />,
    onClick: handlers.onLinkVehicle,
  }

  const edit: RowMenuGroups['edit'] = [
    ...(linkVehicle ? [linkVehicle] : []),
    {
      label: isDoNotContact ? t('contactRowMenu.removeDoNotContact') : t('contactRowMenu.setDoNotContact'),
      icon: <Ban size={16} />,
      onClick: handlers.onToggleDoNotContact,
      // KAN-464 Q3: lifting it on a contact chooses the contact's status,
      // which only who may write the customer role does; setting it is open.
      ...(isDoNotContact && holdsCustomer && !mayWriteCustomerRole
        ? { disabled: true, disabledReason: t('contactRowMenu.removeDoNotContactCustomerRole') }
        : {}),
    },
    ...(canBeBlocked
      ? [
          {
            label: isBlocked ? t('contactRowMenu.removeCreditBlock') : t('contactRowMenu.setCreditBlock'),
            icon: <Lock size={16} />,
            onClick: handlers.onManageCreditBlock,
          },
        ]
      : []),
  ]

  const createFrom: RowMenuGroups['createFrom'] = [
    ...(newOffer ? [newOffer] : []),
    ...(newContract ? [newContract] : []),
    ...(handlers.onMergeInto && canBeMerged
      ? [
          {
            label: t('contactRowMenu.mergeInto'),
            icon: <GitMerge size={16} />,
            onClick: handlers.onMergeInto,
            disabled: isMerged,
            disabledReason: isMerged ? t('contactRowMenu.mergeDisabledMerged') : undefined,
          },
        ]
      : []),
  ]

  const exportPrint: RowMenuGroups['exportPrint'] = [
    { label: t('contactRowMenu.copyContactNumber'), icon: <Copy size={16} />, onClick: handlers.onCopyContactNumber },
  ]

  return {
    primary: newOffer ?? linkVehicle,
    alternative: newOffer ? linkVehicle : undefined,
    overflow: { edit, createFrom, exportPrint },
  }
}
