import type { ReactNode } from 'react'

/** A disabled fieldset around a deal's write controls while the deal is
 * read-only (KAN-426): every input and button inside is disabled, so what
 * only shows sits outside it and stays live (KAN-501). Its own display, so a
 * Stack spaces it like a card. */
export function LockedWhile({ readOnly, children }: { readOnly: boolean; children: ReactNode }) {
  return (
    <fieldset disabled={readOnly} style={{ border: 0, padding: 0, margin: 0, minWidth: 0 }}>
      {children}
    </fieldset>
  )
}
