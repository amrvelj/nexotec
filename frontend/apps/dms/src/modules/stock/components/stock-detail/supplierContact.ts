import type { CustomerRead } from '../../../../api/types'

/** KAN-465: a contact's name as the purchase dialog and the stock read show
 * it — the company, else first and last name, else the number (the server's
 * `customer_display_name`, KAN-140). */
export function supplierLabel(contact: Pick<CustomerRead, 'companyName' | 'firstName' | 'lastName' | 'customerNumber'>): string {
  return contact.companyName || [contact.firstName, contact.lastName].filter(Boolean).join(' ') || contact.customerNumber
}

/** KAN-465 (PRD-Stock, fiktiver Vorsteuerabzug): whether the supplier is
 * VAT-registered, read from the contact — the rule the booking audits an
 * override against (`app/customer/services/supplier_contact.py`): a confirmed
 * VAT status says it; an unconfirmed private individual is not registered;
 * an unconfirmed business is unknown (null), so the user decides. */
export function supplierVatPrefill(
  contact: Pick<CustomerRead, 'customerType' | 'vatRegistered' | 'vatStatusConfirmed'>,
): boolean | null {
  if (contact.vatStatusConfirmed) return contact.vatRegistered
  if (contact.customerType === 'individual') return false
  return null
}
