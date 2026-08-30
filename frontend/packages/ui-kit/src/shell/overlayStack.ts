import type { OverlayEntry } from "./Overlay";

/**
 * The pure half of the overlay stack — `Overlay.tsx`'s `OverlayProvider`
 * is a thin `useState` wrapper around these three functions, split out so
 * the stack ordering itself (which is the part most worth getting exactly
 * right) is testable without mounting a hook.
 */

export function pushEntry(stack: OverlayEntry[], entry: OverlayEntry): OverlayEntry[] {
  return [...stack, entry];
}

/** Pops exactly the top entry. Returns the entry that closed (so the
 * caller can fire its `onClose`) alongside the new stack. */
export function popEntry(stack: OverlayEntry[]): { next: OverlayEntry[]; closed: OverlayEntry | undefined } {
  return { next: stack.slice(0, -1), closed: stack.at(-1) };
}

/** Empties the stack, returning every entry that closed in top-to-bottom
 * order — the same sequence a user closing them one at a time would
 * produce, so a caller's `onClose` handlers see consistent ordering
 * either way. */
export function closeAllEntries(stack: OverlayEntry[]): { closedInOrder: OverlayEntry[] } {
  return { closedInOrder: [...stack].reverse() };
}
