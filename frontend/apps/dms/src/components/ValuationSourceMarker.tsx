import { useTranslation } from 'react-i18next'
import { ValuationSourceBadge } from '@nexotec/ui-kit'
import type { ValuationSourceValue } from '../api/types'

const SOURCES: readonly ValuationSourceValue[] = ['auto_i_dat', 'manual']

function isValuationSource(value: string): value is ValuationSourceValue {
  return (SOURCES as readonly string[]).includes(value)
}

/**
 * KAN-101 (ADR-048 as amended) — "a manual figure is marked manual
 * everywhere it renders". The one marking, with its label translated, for
 * the screens that show a valuation's figure outside the valuation module:
 * Stock's Evaluation tab and the offer's trade-in card. Stock stores the
 * source as a plain string, so an unknown value renders as a dash rather
 * than as a raw enum.
 */
export function ValuationSourceMarker({ source }: { source: string | null | undefined }) {
  const { t } = useTranslation()
  if (!source || !isValuationSource(source)) return <>—</>
  return <ValuationSourceBadge source={source} label={t(`valuationSource.${source}`)} />
}
