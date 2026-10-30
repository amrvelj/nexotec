import { useAuth } from './AuthContext'
import type { GatedCapability } from './gatedCapabilities'

/**
 * KAN-430 (Anto, 2026-10-30, option A) — an action the user's role may never
 * use is hidden; "disabled with a reason" (ADR-061) stays for state, such as
 * a car that has left stock. Presentation only: the API's own gate on each
 * write stays the authority.
 */
export function useCanWrite(capability: GatedCapability): boolean {
  return useAuth().writableCapabilities.includes(capability)
}
