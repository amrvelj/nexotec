import { useCallback } from 'react'
import { useTranslation } from 'react-i18next'
import { fieldHint, type FieldHintProps } from '@nexotec/ui-kit'

/**
 * KAN-443: `const hint = useFieldHint()` then `<TextInput {...hint(t('x'), t('xHint'))} />`
 * — the kit's `fieldHint` with the icon's accessible name in the UI language.
 */
export function useFieldHint(): (label: NonNullable<React.ReactNode>, hintText: React.ReactNode) => FieldHintProps {
  const { t } = useTranslation()
  const icon = t('common.fieldHintIcon')
  return useCallback((label: NonNullable<React.ReactNode>, hintText: React.ReactNode) => fieldHint(label, hintText, { icon }), [icon])
}
