import { useCallback } from 'react'
import { useTranslation } from 'react-i18next'
import { fieldHint, type FieldHintOptions, type FieldHintProps } from '@nexotec/ui-kit'

/**
 * KAN-443: `const hint = useFieldHint()` then `<TextInput {...hint(t('x'), t('xHint'))} />`
 * — the kit's `fieldHint` with the icon's accessible name in the UI language.
 * A Checkbox or Switch passes `{ descriptionId: useId() }` so the hint is linked.
 */
export function useFieldHint(): (label: NonNullable<React.ReactNode>, hintText: React.ReactNode, options?: FieldHintOptions) => FieldHintProps {
  const { t } = useTranslation()
  const icon = t('common.fieldHintIcon')
  return useCallback(
    (label: NonNullable<React.ReactNode>, hintText: React.ReactNode, options?: FieldHintOptions) => fieldHint(label, hintText, { icon }, options),
    [icon],
  )
}
