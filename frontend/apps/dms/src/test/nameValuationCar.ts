import { within } from '@testing-library/react'
import type userEvent from '@testing-library/user-event'
import i18n from '../i18n'

/**
 * KAN-574 (Anto, 2026-11-07, option A): a valuation names its car — the
 * dialog's VIN, make and model are required. A test whose subject is not the
 * car fills whichever of them is still empty and editable (a configuration
 * fills them read-only), so the submit is enabled.
 */
export async function nameValuationCar(
  user: ReturnType<typeof userEvent.setup>,
  dialog: HTMLElement,
  { vin = 'WVWZZZ1KZAW574099', make = 'VW', model = 'Golf' }: { vin?: string; make?: string; model?: string } = {}
) {
  for (const [key, value] of [
    ['valuationCreate.vin', vin],
    ['valuationCreate.make', make],
    ['valuationCreate.model', model],
  ] as const) {
    const input = within(dialog).getByLabelText(i18n.t(key), { exact: false }) as HTMLInputElement
    if (!input.value && !input.readOnly) await user.type(input, value)
  }
}
