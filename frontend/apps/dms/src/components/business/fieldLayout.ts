import type { InputWrapperProps } from '@mantine/core'

/**
 * One layout for every field of the Business screen (KAN-203): the hint
 * sits UNDER the input, never between label and input, so neighbouring
 * fields in a grid keep their inputs on one line whatever their hints say.
 */
export const FIELD: Pick<InputWrapperProps, 'inputWrapperOrder'> = {
  inputWrapperOrder: ['label', 'input', 'description', 'error'],
}
