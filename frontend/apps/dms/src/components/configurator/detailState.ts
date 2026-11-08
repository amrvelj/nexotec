import type { CatalogueSpecificationRead } from '../../api/types'

export type CatalogueDetailName = keyof CatalogueSpecificationRead['details']
export type CatalogueDetailStateRead = CatalogueSpecificationRead['details'][CatalogueDetailName]

/** The key under `configurator.details.state` for a state value. */
export function detailStateLabelKey(state: CatalogueDetailStateRead['state']): string {
  return `configurator.details.state.${state}`
}

/** KAN-211 (ADR-123): the notes the backend writes on a detail state
 * (`catalogue_details.py`), each with its own text; a note outside this list
 * (a failure's class name) is for the call log, never the screen. */
export const DETAIL_NOTE_I18N_KEY: Record<string, string> = {
  entitlement_absent: 'configurator.details.notes.entitlementAbsent',
  variant_unknown_at_provider: 'configurator.details.notes.variantUnknownAtProvider',
  energy_label_ambiguous: 'configurator.details.notes.energyLabelAmbiguous',
}

/** Whether a loaded (`present`) detail carries a note worth a line on
 * screen — the provider's coherent «nothing» (a key it no longer lists). */
export function hasNoteToShow(state: CatalogueDetailStateRead): boolean {
  return state.note != null && Object.hasOwn(DETAIL_NOTE_I18N_KEY, state.note)
}
