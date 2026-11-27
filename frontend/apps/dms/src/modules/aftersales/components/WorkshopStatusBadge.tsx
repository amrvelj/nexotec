import { useTranslation } from 'react-i18next'
import { Badge, type BadgeTone } from '@nexotec/ui-kit'
import type { WorkshopStatus } from '../../../api/types'

const TONE: Record<WorkshopStatus, BadgeTone> = {
  planned: 'slate',
  car_here: 'informational',
  in_work: 'purple',
  work_done: 'warning',
  checked: 'success',
  ready_for_pickup: 'success',
  handed_over: 'slate',
}

/** KAN-813 — the order's workshop status (section 3.9): worked out by the server, never set here. */
export function WorkshopStatusBadge({ status }: { status: WorkshopStatus }) {
  const { t } = useTranslation()
  return (
    <Badge tone={TONE[status]} dot>
      {t(`serviceOrder.workshopStatus.${status}`)}
    </Badge>
  )
}
