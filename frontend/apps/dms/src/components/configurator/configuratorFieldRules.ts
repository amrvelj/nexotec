import type { LABEL_FIELD } from '../../hooks/useReferenceValueOptions'
import type { CODED_FIELDS, SPEC_FIELD_GROUPS } from '../../configurationOptions'
import type { ReferenceValueRead } from '../../api/types'
import type { Draft } from './configuratorDraft'

export type RefLabelRows = Record<string, { valueCode: string; label: string; active: boolean }[] | null>
export type CodedValueRows = Record<string, ReferenceValueRead[] | null>
export type LabelField = (typeof LABEL_FIELD)[keyof typeof LABEL_FIELD]
export type SpecFieldGroupDef = (typeof SPEC_FIELD_GROUPS)[number]
export type CodedFieldDef = (typeof CODED_FIELDS)[number]

// --- field rules (pure; KAN-335) -------------------------------------

/** A list that failed to load marks its codes `⚠`, as does a code missing
 * from its loaded list — never the raw code as if it were a label. Until
 * the lists arrive, the code stands in. */
export function refLabelOf(refLabels: RefLabelRows | undefined, listCode: string, code: string): string {
  if (!refLabels) return code
  const label = refLabels[listCode]?.find((r) => r.valueCode === code)?.label
  return label ?? `⚠ ${code}`
}

/** KAN-239 — a spec reference field offers its list's active values; a
 * stored value retired since stays visible, disabled (the KAN-96 pattern). */
export function specRefOptionsOf(refLabels: RefLabelRows | undefined, listCode: string, current: string | null) {
  const rows = refLabels?.[listCode] ?? []
  const data: { value: string; label: string; disabled?: boolean }[] = rows
    .filter((r) => r.active)
    .map((r) => ({ value: r.valueCode, label: r.label }))
  if (current != null && !data.some((d) => d.value === current)) {
    data.push({ value: current, label: refLabelOf(refLabels, listCode, current), disabled: true })
  }
  return data
}

/** KAN-245 — the API refuses a save that carries a spec reference value
 * that is not active (R5: one sent back unchanged included). A manual
 * configuration sends its whole spec block; a provider one only the fields
 * the advisor overrode. A list that failed to load flags nothing. */
export function specRefWillBeSent(source: Draft['source'], overridden: Set<string>, key: string): boolean {
  return source === 'manual' || overridden.has(key)
}

export function specRefRetiredOf(refLabels: RefLabelRows | undefined, listCode: string, current: string | null): boolean {
  const rows = refLabels?.[listCode]
  if (current == null || rows == null) return false
  return !rows.some((r) => r.valueCode === current && r.active)
}

/** KAN-96 — a coded field offers the active values of its list, the same
 * set the API accepts. A stored value outside that set (retired by the
 * admin, or never on the list) stays visible as its code, disabled and
 * flagged with a field error, because the API refuses a save that carries
 * it (Anto, 2026-10-07). `⚠ <code>` is kept for the other case, an active
 * value with no label in this language. A list that failed to load says so
 * on its field and flags nothing — the API remains the authority. */
export function codedOptionsOf(
  codedValues: CodedValueRows | undefined,
  labelField: LabelField,
  listCode: string,
  current: string | null,
) {
  const data: { value: string; label: string; disabled?: boolean }[] = [...(codedValues?.[listCode] ?? [])]
    .sort((a, b) => a.sortOrder - b.sortOrder)
    .map((r) => ({
      value: r.valueCode,
      label: r[labelField].trim() !== '' ? r[labelField] : `⚠ ${r.valueCode}`,
    }))
  if (current != null && !data.some((d) => d.value === current)) {
    data.push({ value: current, label: current, disabled: true })
  }
  return data
}

export function codedValueRetiredOf(codedValues: CodedValueRows | undefined, listCode: string, current: string | null): boolean {
  const rows = codedValues?.[listCode]
  if (current == null || rows == null) return false
  return !rows.some((r) => r.valueCode === current)
}
