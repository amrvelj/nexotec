import type { ReactNode } from 'react'
import { Stack, Text } from '@mantine/core'
import { useTranslation } from 'react-i18next'
import { RepeatableRowGroup } from '@nexotec/ui-kit'
import { ApiError } from '../../api/client'

export interface ContactPointRow<TType extends string> {
  id: string
  type: TType
  value: string
  label?: string | null
  isPrimary: boolean
  validTo?: string | null
  doNotUse?: boolean
  doNotUseReason?: string | null
  consentGranted: boolean
  consentSource?: string | null
  consentTimestamp?: string | null
}

/** The PATCH shape a caller's `onUpdate` receives — every ADR-067 field,
 * not just `type`/`value`/`isPrimary` (matches CustomerPhoneUpdate/
 * CustomerEmailUpdate in app/customer/schemas/customer.py). */
export interface ContactPointUpdatePatch<TType extends string> {
  type?: TType
  value?: string
  label?: string | null
  isPrimary?: boolean
  validTo?: string | null
  doNotUse?: boolean
  doNotUseReason?: string | null
  consentGranted?: boolean
  consentSource?: string | null
}

interface ContactPointsEditorProps<TType extends string> {
  label: string
  addLabel: string
  typeOptions: { value: TType; label: string }[]
  rows: ContactPointRow<TType>[]
  newRowType: TType
  renderValueEditor: (value: string, onChange: (v: string) => void, autoFocus: boolean) => ReactNode
  onCreate: (row: { type: TType; value: string }) => Promise<void>
  onUpdate: (id: string, patch: ContactPointUpdatePatch<TType>) => Promise<void>
  onDelete: (id: string) => Promise<void>
}

/**
 * FR-07: add/edit/delete phone numbers and email addresses independently
 * of the main record. WP-6c: this is now a thin, app-specific wrapper
 * (the section heading, the translated labels, the ApiError unwrapping)
 * around ui-kit's `RepeatableRowGroup` — the full ADR-067 contract (the
 * primary star, close-not-delete, consent-on-the-row, former-rows-behind-
 * a-toggle) lives there, once, shared with whatever module builds its own
 * repeatable field next.
 */
export function ContactPointsEditor<TType extends string>({
  label,
  addLabel,
  typeOptions,
  rows,
  newRowType,
  renderValueEditor,
  onCreate,
  onUpdate,
  onDelete,
}: ContactPointsEditorProps<TType>) {
  const { t } = useTranslation()

  return (
    <Stack gap="xs">
      <Text size="sm" fw={600}>
        {label}
      </Text>
      <RepeatableRowGroup
        mode="detail"
        label={label}
        addLabel={addLabel}
        formerLabel={t('customerDetail.contactPoints.former')}
        typeOptions={typeOptions}
        defaultType={newRowType}
        rows={rows}
        renderValueEditor={renderValueEditor}
        onCreate={(draft) => onCreate({ type: draft.type as TType, value: draft.value })}
        onUpdate={(id, patch) => onUpdate(id, { ...patch, type: patch.type as TType | undefined })}
        onDelete={onDelete}
        describeError={(err) => (err instanceof ApiError ? err.message : t('customerDetail.errors.somethingWentWrong'))}
        labels={{
          primary: t('customerDetail.contactPoints.primary'),
          noLongerValid: t('customerDetail.contactPoints.noLongerValid'),
          doesNotWork: t('customerDetail.contactPoints.doesNotWork'),
          doesNotWorkReasonPlaceholder: t('customerDetail.contactPoints.doesNotWorkReasonPlaceholder'),
          delete: t('customerDetail.contactPoints.delete'),
          consent: t('customerDetail.contactPoints.consent'),
          save: t('customerDetail.contactPoints.save'),
          cancel: t('customerDetail.contactPoints.cancel'),
          confirm: t('customerDetail.contactPoints.confirm'),
          none: t('customerDetail.contactPoints.none'),
          labelFieldPlaceholder: t('customerDetail.contactPoints.labelPlaceholder'),
        }}
      />
    </Stack>
  )
}
