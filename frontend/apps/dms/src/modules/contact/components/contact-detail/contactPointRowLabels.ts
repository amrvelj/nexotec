import type { RepeatableRowGroupLabels } from '@nexotec/ui-kit'

/**
 * A contact's phone and e-mail rows' strings in the user's UI language — the
 * create dialog and the detail screen render the same `RepeatableRowGroup`
 * with the same words, and the kit ships no English of its own (KAN-364).
 */
export function contactPointRowLabels(t: (key: string, options?: Record<string, unknown>) => string): RepeatableRowGroupLabels {
  return {
    primary: t('contactDetail.contactPoints.primary'),
    noLongerValid: t('contactDetail.contactPoints.noLongerValid'),
    doesNotWork: t('contactDetail.contactPoints.doesNotWork'),
    doesNotWorkReasonPlaceholder: t('contactDetail.contactPoints.doesNotWorkReasonPlaceholder'),
    delete: t('contactDetail.contactPoints.delete'),
    consent: t('contactDetail.contactPoints.consent'),
    consentScope: t('contactDetail.contactPoints.consentScope'),
    consentSource: t('contactDetail.contactPoints.consentSource'),
    save: t('contactDetail.contactPoints.save'),
    cancel: t('contactDetail.contactPoints.cancel'),
    confirm: t('contactDetail.contactPoints.confirm'),
    none: t('contactDetail.contactPoints.none'),
    genericError: t('contactDetail.errors.somethingWentWrong'),
    // KAN-460: the columns are named in a header row, the label and consent
    // columns with a hint — «Bezeichnung» says WHICH number this is.
    typeColumn: t('contactDetail.contactPoints.typeColumn'),
    labelColumn: t('contactDetail.contactPoints.labelColumn'),
    labelHint: t('contactDetail.contactPoints.labelHint'),
    consentHint: t('contactDetail.contactPoints.consentHint'),
    hintIcon: t('common.fieldHintIcon'),
    rowActions: (label: string) => t('contactDetail.contactPoints.rowActions', { label }),
  }
}
