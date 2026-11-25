import { formatCurrencyChf } from '@nexotec/ui-kit'

/** A rate as `CHF 148.50`; one typed with more than two decimals (the API
 * takes four, decision 23) is shown with them, never rounded on screen. */
export function rateAmountText(amount: string): string {
  const value = Number(amount)
  const cents = Math.round(value * 100)
  if (Math.abs(value * 100 - cents) < 1e-9) return formatCurrencyChf(value)
  const [whole, fraction = ''] = amount.split('.')
  return `CHF ${whole.replace(/\B(?=(\d{3})+(?!\d))/g, "'")}.${fraction.replace(/0+$/, '')}`
}
