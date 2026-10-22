import type { RepeatableRowGroupLabels } from '@nexotec/ui-kit'

/**
 * A customer's phone and e-mail rows' strings in the user's UI language — the
 * create dialog and the detail screen render the same `RepeatableRowGroup`
 * with the same words, and the kit ships no English of its own (KAN-364).
 */
export function contactPointRowLabels(t: (key: string, options?: Record<string, unknown>) => string): RepeatableRowGroupLabels {
  return {
    primary: t('customerDetail.contactPoints.primary'),
    noLongerValid: t('customerDetail.contactPoints.noLongerValid'),
    doesNotWork: t('customerDetail.contactPoints.doesNotWork'),
    doesNotWorkReasonPlaceholder: t('customerDetail.contactPoints.doesNotWorkReasonPlaceholder'),
    delete: t('customerDetail.contactPoints.delete'),
    consent: t('customerDetail.contactPoints.consent'),
    consentScope: t('customerDetail.contactPoints.consentScope'),
    consentSource: t('customerDetail.contactPoints.consentSource'),
    save: t('customerDetail.contactPoints.save'),
    cancel: t('customerDetail.contactPoints.cancel'),
    confirm: t('customerDetail.contactPoints.confirm'),
    none: t('customerDetail.contactPoints.none'),
    genericError: t('customerDetail.errors.somethingWentWrong'),
    labelFieldPlaceholder: t('customerDetail.contactPoints.labelPlaceholder'),
    rowActions: (label: string) => t('customerDetail.contactPoints.rowActions', { label }),
  }
}
