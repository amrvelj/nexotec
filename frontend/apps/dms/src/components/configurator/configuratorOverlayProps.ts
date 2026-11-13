import type { ConfiguratorMode } from '../../configurationOptions'
import type { ConfigurationRead } from '../../api/types'

/**
 * KAN-557 — a host that corrects the car's VIN through its own action owns
 * the configuration's VIN: the overlay never sends `vin` in its PATCH. A VIN
 * the user changed is handed to the host after the configuration's own saves
 * (fields, then options); the overlay closes only once the host resolves.
 * Applies to a saved configuration (`existing`) only. What the host says
 * comes from the host — the overlay names no host.
 */
export interface HostOwnedVin {
  /** Called with the changed VIN, normalised. Resolves once the host has
   * corrected it — the overlay then re-reads the configuration (its new
   * version and VIN) and commits; rejects with a `HostCommitError` when it
   * did not (cancelled, refused): the overlay stays open, the other fields
   * saved, and shows that message. */
  correctVin: (vin: string) => Promise<void>
  /** The VIN field's hint: how a changed VIN is applied. */
  hint: string
  /** Said in place when the VIN is emptied: the host cannot clear it. */
  clearRefused: string
  /** Set when this user may not correct the VIN: the field is read-only and
   * its hint is this reason. */
  readOnlyReason?: string
}

export interface ConfiguratorOverlayProps {
  /** The modes this host allows (PRD v1.4 mode matrix): offer Path B
   * `['build']`, valuation and the offer's trade-in `['record']`, the stock
   * pipeline both. With one mode there is **no mode switch at all**. */
  allowedModes?: ConfiguratorMode[]
  /** Prefilled from the host, changeable in phase 1 among `allowedModes`. */
  initialMode?: ConfiguratorMode
  /** C-F — reopen a saved configuration in phase 2 (edit, re-sync). */
  existing?: ConfigurationRead | null
  /** KAN-468 — a host that already knows the car (a valuation requested
   * from the vehicle) prefills phase 1's identification input with its VIN,
   * so nobody types it twice. */
  initialVin?: string
  /** Called with the saved configuration when the advisor commits. May be
   * async; a rejected `HostCommitError` is shown in the overlay. */
  onCommitted: (configuration: ConfigurationRead) => void | Promise<void>
  /** KAN-557 — the host corrects the VIN through its own action
   * (`HostOwnedVin`). Absent: the VIN is the configuration's, in its PATCH. */
  hostOwnsVin?: HostOwnedVin
  /** Close without committing (the overlay's close button / Escape also
   * calls this via the host's `useOverlay().pop`). */
  onClose: () => void
}
