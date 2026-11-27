import { Ban, Car, ExternalLink, UserCheck } from 'lucide-react'
import type { RowMenuGroups } from '@nexotec/ui-kit'
import type { ServiceOrderState, WorkshopStatus } from '../../../api/types'
import { OPEN_STATES } from '../serviceOrderOptions'

type Translate = (key: string, options?: Record<string, unknown>) => string

interface RowMenuOrder {
  state: ServiceOrderState
  workshopStatus?: WorkshopStatus
}

// KAN-813: the car is in the house while its stay runs — «Fahrzeug ist da» would receive it twice.
const CAR_HERE: ReadonlySet<WorkshopStatus> = new Set(['car_here', 'in_work', 'work_done', 'checked', 'ready_for_pickup'])

export interface ServiceOrderRowMenuHandlers {
  onOpen?: () => void
  /** D8 «Antwort erfassen» (KAN-811). Omitted when the role may not write `service_orders`. */
  onRecordAnswer?: () => void
  /** D14 «Fahrzeug ist da» (KAN-813). Omitted when the role may not write `service_orders`. */
  onArrive?: () => void
  /** Omitted when the role may not write `service_orders`: the action is hidden (ADR-104). */
  onCancel?: () => void
}

/**
 * ADR-061 — the workshop order's row menu, the one definition the list's
 * rows and the order page's overflow both render. In this slice it carries
 * «Öffnen», «Antwort erfassen» (KAN-811), «Fahrzeug ist da» (KAN-813) and
 * «Auftrag stornieren» (the menu entries from «An Kunden senden» down are
 * later tickets). Cancelling is open in «Entwurf» and
 * «Angenommen» (table 3.1); in another state the entry is disabled with its
 * reason. A clock that has run is the server's refusal, said in one sentence.
 */
export function buildServiceOrderRowMenu(t: Translate, order: RowMenuOrder, handlers: ServiceOrderRowMenuHandlers): RowMenuGroups {
  const groups: RowMenuGroups = {}
  if (handlers.onOpen) {
    groups.navigate = [{ label: t('serviceOrder.actions.open'), icon: <ExternalLink size={16} />, onClick: handlers.onOpen }]
  }
  const open = OPEN_STATES.has(order.state)
  const edit = []
  if (handlers.onRecordAnswer) {
    edit.push({
      label: t('serviceOrder.actions.recordAnswer'),
      icon: <UserCheck size={16} />,
      onClick: handlers.onRecordAnswer,
      disabled: !open,
      disabledReason: open ? undefined : t('serviceOrder.actions.frozen'),
    })
  }
  if (handlers.onArrive) {
    const here = order.workshopStatus !== undefined && CAR_HERE.has(order.workshopStatus)
    const reason = !open ? t('serviceOrder.actions.frozen') : here ? t('serviceOrder.arrival.alreadyHere') : undefined
    edit.push({
      label: t('serviceOrder.arrival.title'),
      icon: <Car size={16} />,
      onClick: handlers.onArrive,
      disabled: reason !== undefined,
      disabledReason: reason,
    })
  }
  if (edit.length) groups.edit = edit
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
