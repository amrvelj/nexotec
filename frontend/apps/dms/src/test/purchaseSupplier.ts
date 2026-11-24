import { fireEvent, screen } from '@testing-library/react'
import type { ContactRead } from '../api/types'
import i18n from '../i18n'
import type { FakeRoute } from './fakeBackend'
import { contact } from './fixtures'

/** KAN-465: the supplier contact the purchase dialog's tests pick — a private
 * individual holding the supplier role (VAT prefill: not registered). */
export const SUPPLIER: ContactRead = contact({
  id: '01a20000-0000-7000-8000-000000000465',
  contactNumber: 'K-0465',
  firstName: 'Hans',
  lastName: 'Muster',
  roles: ['supplier'],
})

/** The contact search the dialog's picker reads (`GET /contacts?q=…`). */
export function supplierSearchRoute(contacts: ContactRead[] = [SUPPLIER]): FakeRoute {
  return {
    method: 'GET',
    match: /^\/contacts$/,
    handler: () => ({ items: contacts, nextCursor: null, total: contacts.length }),
  }
}

/** Opens «Kontakt suchen», types the name and picks the contact. */
export async function pickSupplier(contact: ContactRead = SUPPLIER): Promise<void> {
  fireEvent.click(screen.getByRole('button', { name: i18n.t('stockDetail.purchase.supplier.search') }))
  const input = await screen.findByRole('textbox', { name: i18n.t('stockDetail.purchase.supplier.search') })
  fireEvent.change(input, { target: { value: contact.lastName ?? contact.contactNumber } })
  fireEvent.click(await screen.findByRole('option', { name: new RegExp(contact.contactNumber) }, { timeout: 3000 }))
}
