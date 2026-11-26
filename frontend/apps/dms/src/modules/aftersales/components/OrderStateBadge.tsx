import { useTranslation } from 'react-i18next'
import { ServiceOrderStateBadge } from '@nexotec/ui-kit'
import type { ServiceOrderState } from '../../../api/types'

/** The workshop order's state (table 3.1) in the UI language: the kit's badge, the app's label. */
export function OrderStateBadge({ state }: { state: ServiceOrderState }) {
  const { t } = useTranslation()
  return <ServiceOrderStateBadge state={state} label={t(`serviceOrder.state.${state}`)} />
}
