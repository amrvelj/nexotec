import { Ban, ExternalLink } from 'lucide-react'
import type { RowMenuGroups } from '@nexotec/ui-kit'
import type { ServiceOrderState } from '../../../api/types'
import { OPEN_STATES } from '../serviceOrderOptions'

type Translate = (key: string, options?: Record<string, unknown>) => string

interface RowMenuOrder {
  state: ServiceOrderState
}

export interface ServiceOrderRowMenuHandlers {
  onOpen?: () => void
  /** Omitted when the role may not write `service_orders`: the action is hidden (ADR-104). */
  onCancel?: () => void
}

/**
 * ADR-061 — the workshop order's row menu, the one definition the list's
 * rows and the order page's overflow both render. In this slice it carries
 * «Öffnen» and «Auftrag stornieren» (the menu entries from «An Kunden
 * senden» down are later tickets). Cancelling is open in «Entwurf» and
 * «Angenommen» (table 3.1); in another state the entry is disabled with its
 * reason. A clock that has run is the server's refusal, said in one sentence.
 */
export function buildServiceOrderRowMenu(t: Translate, order: RowMenuOrder, handlers: ServiceOrderRowMenuHandlers): RowMenuGroups {
  const groups: RowMenuGroups = {}
  if (handlers.onOpen) {
    groups.navigate = [{ label: t('serviceOrder.actions.open'), icon: <ExternalLink size={16} />, onClick: handlers.onOpen }]
  }
  if (handlers.onCancel) {
    const open = OPEN_STATES.has(order.state)
    groups.destructive = [
      {
        label: t('serviceOrder.actions.cancel'),
        icon: <Ban size={16} />,
        onClick: handlers.onCancel,
        disabled: !open,
        disabledReason: open ? undefined : t(`serviceOrder.actions.cancelNotInState.${order.state}`),
      },
    ]
  }
  return groups
}
