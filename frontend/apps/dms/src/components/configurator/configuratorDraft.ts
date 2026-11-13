import type { CodedFieldKey, ConfiguratorMode } from '../../configurationOptions'
import type {
  CatalogueOptionRead,
  CatalogueSpecificationRead,
  CatalogueVariantRead,
  ConfigurationMatchMethod,
  ConfigurationOptionInput,
  ConfigurationOptionRead,
  ConfigurationRead,
  VehicleSpecBlockRead,
} from '../../api/types'
import type { IdentifiedStart } from './IdentificationPanel'

export type CodedValues = Record<CodedFieldKey, string | null>

export type Phase = 'find' | 'configure'
export type Section = 'specification' | 'options' | 'colour' | 'images' | 'summary'

export interface Draft {
  source: 'provider' | 'manual'
  catalogueVariantId: string | null
  brandDisplayName: string | null
  modelGroupName: string | null
  variantName: string | null
  spec: VehicleSpecBlockRead
  /** spec keys the advisor changed after a catalogue copy */
  overridden: Set<string>
  /** KAN-96 — the five coded fields. Picked from their reference lists on a
   * manual configuration, all five required (Anto, 2026-10-12); on a
   * provider one they are the catalogue's, shown read-only and never sent
   * back (the API refuses a change, `coded_field_locked`). */
  coded: CodedValues
  vin: string
  firstRegistrationDate: string
  mileageKm: string
  exteriorColour: string
  interiorColour: string
  /** KAN-211 — the hand-named colour types (`ColourWheelsFields`); `''` is not set. */
  exteriorColourType: string
  interiorColourType: string
  /** Empty string means "not set" (matches `mileageKm`'s own convention) —
   * `''` never reaches the wire as `NaN`, `save()` maps it to `null`. */
  exteriorColourSurcharge: string
  interiorColourSurcharge: string
  wheels: string
  wheelsSurcharge: string
  notes: string
  /** C-D (KAN-42) — what identification established, kept as observed
   * data, and how the car was identified (kept permanently). */
  licencePlate: string
  stammnummer: string
  typeApprovalNumber: string
  matchMethod: ConfigurationMatchMethod | null
  confirmedBestMatchCode: number | null
  /** Explicit overrides only (KAN-43/C-E) — see `OptionsTabProps.selected`'s
   * own doc comment for what "explicit" means here. */
  selectedOptions: Map<string, ConfigurationOptionInput>
}

const EMPTY_SPEC: VehicleSpecBlockRead = {}

function codedFrom(source: CodedValues): CodedValues {
  return {
    vehicleKind: source.vehicleKind,
    fuelType: source.fuelType,
    bodyStyle: source.bodyStyle,
    drivetrain: source.drivetrain,
    transmission: source.transmission,
  }
}

export function draftFromVariant(v: CatalogueVariantRead): Draft {
  return {
    source: 'provider',
    catalogueVariantId: v.id,
    brandDisplayName: v.brandDisplayName,
    modelGroupName: v.modelGroupName,
    variantName: v.variantName,
    spec: { ...v.spec },
    overridden: new Set(),
    coded: codedFrom(v),
    vin: '',
    firstRegistrationDate: '',
    mileageKm: '',
    exteriorColour: '',
    interiorColour: '',
    exteriorColourType: '',
    interiorColourType: '',
    exteriorColourSurcharge: '',
    interiorColourSurcharge: '',
    wheels: '',
    wheelsSurcharge: '',
    notes: '',
    licencePlate: '',
    stammnummer: '',
    typeApprovalNumber: '',
    matchMethod: null,
    confirmedBestMatchCode: null,
    selectedOptions: new Map(),
  }
}

export function emptyManualDraft(): Draft {
  return {
    source: 'manual',
    catalogueVariantId: null,
    brandDisplayName: '',
    modelGroupName: '',
    variantName: '',
    spec: { ...EMPTY_SPEC },
    overridden: new Set(),
    coded: { vehicleKind: null, fuelType: null, bodyStyle: null, drivetrain: null, transmission: null },
    vin: '',
    firstRegistrationDate: '',
    mileageKm: '',
    exteriorColour: '',
    interiorColour: '',
    exteriorColourType: '',
    interiorColourType: '',
    exteriorColourSurcharge: '',
    interiorColourSurcharge: '',
    wheels: '',
    wheelsSurcharge: '',
    notes: '',
    licencePlate: '',
    stammnummer: '',
    typeApprovalNumber: '',
    matchMethod: null,
    confirmedBestMatchCode: null,
    selectedOptions: new Map(),
  }
}

/** C-F — reopening a saved configuration: phase 2, every field as stored,
 * catalogue options keyed by the variant option they came from. */
export function draftFromRead(c: ConfigurationRead): Draft {
  const selectedOptions = new Map<string, ConfigurationOptionInput>()
  for (const o of c.options) {
    if (o.variantOptionId) {
      selectedOptions.set(o.variantOptionId, {
        variantOptionId: o.variantOptionId,
        optionCode: o.optionCode,
        description: o.description,
        optionGroup: o.optionGroup,
        price: o.price,
        isIncluded: o.isIncluded,
        isPackage: o.isPackage,
        selected: o.selected,
        equipmentFeatures: [...o.equipmentFeatures],
      })
    }
  }
  return {
    source: c.source,
    catalogueVariantId: c.catalogueVariantId,
    brandDisplayName: c.brandDisplayName,
    modelGroupName: c.modelGroupName,
    variantName: c.variantName,
    spec: { ...c.spec },
    overridden: new Set(c.overriddenFields),
    coded: codedFrom(c),
    vin: c.vin ?? '',
    firstRegistrationDate: c.firstRegistrationDate ?? '',
    mileageKm: c.mileageKm != null ? String(c.mileageKm) : '',
    exteriorColour: c.exteriorColour ?? '',
    interiorColour: c.interiorColour ?? '',
    exteriorColourType: c.exteriorColourType ?? '',
    interiorColourType: c.interiorColourType ?? '',
    exteriorColourSurcharge: c.exteriorColourSurcharge ?? '',
    interiorColourSurcharge: c.interiorColourSurcharge ?? '',
    wheels: c.wheels ?? '',
    wheelsSurcharge: c.wheelsSurcharge ?? '',
    notes: c.notes ?? '',
    licencePlate: c.licencePlate ?? '',
    stammnummer: c.stammnummer ?? '',
    typeApprovalNumber: c.typeApprovalNumber ?? '',
    matchMethod: c.matchMethod,
    confirmedBestMatchCode: null,
    selectedOptions,
  }
}

export function withObserved(draft: Draft, start: IdentifiedStart): Draft {
  const o = start.observed
  return {
    ...draft,
    vin: o.vin ?? draft.vin,
    licencePlate: o.licencePlate ?? '',
    stammnummer: o.stammnummer ?? '',
    typeApprovalNumber: o.typeApprovalNumber ?? '',
    firstRegistrationDate: o.firstRegistrationDate ?? draft.firstRegistrationDate,
    matchMethod: start.matchMethod,
    confirmedBestMatchCode: start.confirmedBestMatchCode ?? null,
  }
}

/** The spec fields the advisor changed after a catalogue copy — a cleared
 * one as `null`, which the API reads as "clear it" (KAN-247). */
export function pickSpec(spec: VehicleSpecBlockRead, keys: Set<string>): Record<string, unknown> {
  const out: Record<string, unknown> = {}
  for (const k of keys) out[k] = emptyToNull((spec as Record<string, unknown>)[k])
  return out
}

/** A manual configuration's whole spec block, every empty field as `null`:
 * a field the advisor cleared is cleared on the server too (KAN-247); one
 * that was never set is null on both sides, which changes nothing. */
export function fullSpec(spec: VehicleSpecBlockRead): Record<string, unknown> {
  const out: Record<string, unknown> = {}
  for (const [k, v] of Object.entries(spec)) out[k] = emptyToNull(v)
  return out
}

function emptyToNull(v: unknown): unknown {
  return v === undefined || v === '' ? null : v
}

export function toConfigurationOptionInput(option: CatalogueOptionRead): ConfigurationOptionInput {
  return {
    variantOptionId: option.id,
    optionCode: option.optionCode,
    description: option.description,
    optionGroup: option.optionGroup,
    price: option.price,
    isIncluded: option.isIncluded,
    isPackage: option.isPackage,
    selected: true,
    equipmentFeatures: [...option.equipmentFeatures],
  }
}

/** Every included option (always) plus whatever the advisor explicitly
 * selected — see `OptionsTabProps.selected`'s own doc comment on why the
 * draft only ever tracks explicit overrides. */
export function finalOptionsPayload(
  draft: Draft,
  spec: CatalogueSpecificationRead | undefined,
): ConfigurationOptionInput[] {
  if (!spec) return []
  return spec.options
    .filter((o) => o.isIncluded || draft.selectedOptions.get(o.id)?.selected === true)
    .map((o) => draft.selectedOptions.get(o.id) ?? toConfigurationOptionInput(o))
}

function previewOptions(draft: Draft, spec: CatalogueSpecificationRead | undefined): ConfigurationOptionRead[] {
  return finalOptionsPayload(draft, spec).map((o, sequence) => ({
    id: o.variantOptionId ?? `preview-${sequence}`,
    sequence,
    variantOptionId: o.variantOptionId ?? null,
    optionCode: o.optionCode ?? null,
    description: o.description,
    optionGroup: o.optionGroup ?? null,
    price: o.price != null ? String(o.price) : null,
    isIncluded: o.isIncluded ?? false,
    isPackage: o.isPackage ?? false,
    selected: o.selected ?? true,
    equipmentFeatures: o.equipmentFeatures ?? [],
    // KAN-595: the catalogue row's own texts, so the preview names the option
    // as the saved configuration will (the server copies the same row's).
    descriptions: spec?.options.find((c) => c.id === o.variantOptionId)?.descriptions,
  }))
}

/** A client-side `ConfigurationRead` shape for the summary card preview,
 * before the draft is saved. */
export function previewRead(draft: Draft, mode: ConfiguratorMode, spec: CatalogueSpecificationRead | undefined): ConfigurationRead {
  return {
    id: 'draft',
    tenantId: 'draft',
    source: draft.source,
    mode,
    catalogueMatchStatus:
      draft.source !== 'provider'
        ? 'unverified'
        : draft.confirmedBestMatchCode === 2
          ? 'best_match_confirmed'
          : 'matched',
    matchMethod: draft.matchMethod ?? (draft.source === 'provider' ? 'catalogue_browse' : 'manual'),
    catalogueVariantId: draft.catalogueVariantId,
    catalogueVariantLabel: null,
    vehicleId: null,
    vehicleLabel: null,
    vin: draft.vin || null,
    stammnummer: draft.stammnummer || null,
    typeApprovalNumber: draft.typeApprovalNumber || null,
    firstRegistrationDate: draft.firstRegistrationDate || null,
    licencePlate: draft.licencePlate || null,
    mileageKm: draft.mileageKm ? Number(draft.mileageKm) : null,
    brandDisplayName: draft.brandDisplayName,
    modelGroupName: draft.modelGroupName,
    variantName: draft.variantName,
    exteriorColour: draft.exteriorColour || null,
    interiorColour: draft.interiorColour || null,
    exteriorColourType: draft.exteriorColourType || null,
    interiorColourType: draft.interiorColourType || null,
    exteriorColourSurcharge: draft.exteriorColourSurcharge || null,
    interiorColourSurcharge: draft.interiorColourSurcharge || null,
    wheels: draft.wheels || null,
    wheelsSurcharge: draft.wheelsSurcharge || null,
    ...draft.coded,
    spec: draft.spec,
    overriddenFields: [...draft.overridden],
    options: previewOptions(draft, spec),
    notes: draft.notes || null,
    version: 0,
    createdAt: new Date().toISOString(),
    updatedAt: new Date().toISOString(),
  }
}
