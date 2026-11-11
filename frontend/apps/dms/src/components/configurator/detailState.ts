import type { CatalogueSpecificationRead } from '../../api/types'

export type CatalogueDetailName = keyof CatalogueSpecificationRead['details']
export type CatalogueDetailStateRead = CatalogueSpecificationRead['details'][CatalogueDetailName]

/** The key under `configurator.details.state` for a state value. */
export function detailStateLabelKey(state: CatalogueDetailStateRead['state']): string {
  return `configurator.details.state.${state}`
}

/** KAN-211 (ADR-123): the notes the backend writes on a detail state
 * (`catalogue_details.py`), each with its own text; a note outside this list
 * (any other failure's class name) is for the call log, never the screen.
 *
 * KAN-598 (Anto, 2026-11-11, option A): `ProviderRequestError` is the note
 * `_load_one` writes on a `present` detail the provider refused with a
 * 400/422 (`CatalogueDetailStateRead.note` documents it as one of the
 * present notes). It reads as one translated line — the class name itself
 * never reaches the screen — so the refusal is not mistaken for a clean
 * empty load. */
export const DETAIL_NOTE_I18N_KEY: Record<string, string> = {
  entitlement_absent: 'configurator.details.notes.entitlementAbsent',
  variant_unknown_at_provider: 'configurator.details.notes.variantUnknownAtProvider',
  energy_label_ambiguous: 'configurator.details.notes.energyLabelAmbiguous',
  ProviderRequestError: 'configurator.details.notes.providerRequestRefused',
}

/** Whether a loaded (`present`) detail carries a note worth a line on
 * screen — the provider's coherent «nothing» (a key it no longer lists, a
 * request it refused). */
export function hasNoteToShow(state: CatalogueDetailStateRead): boolean {
  return state.note != null && Object.hasOwn(DETAIL_NOTE_I18N_KEY, state.note)
}
