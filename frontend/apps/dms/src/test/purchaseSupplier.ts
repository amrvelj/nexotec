import { fireEvent, screen } from '@testing-library/react'
import type { CustomerRead } from '../api/types'
import i18n from '../i18n'
import type { FakeRoute } from './fakeBackend'
import { customer } from './fixtures'

/** KAN-465: the supplier contact the purchase dialog's tests pick — a private
 * individual holding the supplier role (VAT prefill: not registered). */
export const SUPPLIER: CustomerRead = customer({
  id: '01a20000-0000-7000-8000-000000000465',
  customerNumber: 'K-0465',
  firstName: 'Hans',
  lastName: 'Muster',
  roles: ['supplier'],
})

/** The contact search the dialog's picker reads (`GET /customers?q=…`). */
export function supplierSearchRoute(contacts: CustomerRead[] = [SUPPLIER]): FakeRoute {
  return {
    method: 'GET',
    match: /^\/customers$/,
    handler: () => ({ items: contacts, nextCursor: null, total: contacts.length }),
  }
}

/** Opens «Kontakt suchen», types the name and picks the contact. */
export async function pickSupplier(contact: CustomerRead = SUPPLIER): Promise<void> {
  fireEvent.click(screen.getByRole('button', { name: i18n.t('stockDetail.purchase.supplier.search') }))
  const input = await screen.findByRole('textbox', { name: i18n.t('stockDetail.purchase.supplier.search') })
  fireEvent.change(input, { target: { value: contact.lastName ?? contact.customerNumber } })
  fireEvent.click(await screen.findByRole('option', { name: new RegExp(contact.customerNumber) }, { timeout: 3000 }))
}
