import { useEffect, type RefObject } from "react";

/**
 * KAN-605 — who an Escape keypress belongs to, shared by the Overlay and the
 * kit's own popover panels. Mantine's Popover, Combobox, Menu and Modal
 * close themselves on Escape but never stop the event or prevent its
 * default, so every enclosing listener sees the same keypress; each one
 * must decide whether the Escape was meant for something nested inside it.
 *
 * Read in the capture phase, before the popup's own handler runs: a Select
 * closes its dropdown in React's keydown handler, which flushes
 * synchronously, so at bubble time its input would already read closed.
 */

/** A popup trigger whose popup is open: a Popover's or Menu's button
 * (`aria-haspopup` + `aria-expanded="true"`), a Select's or Autocomplete's
 * input (Mantine's Combobox marks it `aria-haspopup` + `data-expanded="true"`
 * and `data-mantine-stop-propagation`, the flag Mantine's own Modal honours).
 * A plain disclosure (`aria-expanded` without `aria-haspopup`) is not a
 * popup: Escape never closes it, so it never takes the Escape. */
export const OPEN_POPUP_TRIGGER =
  '[aria-haspopup][aria-expanded="true"], [aria-haspopup][data-expanded="true"], [data-mantine-stop-propagation="true"]';

/** Whether an Escape on `target` is the dismissal of a popup opened inside
 * `container` (an overlay layer, a kit panel's dropdown) rather than a
 * request to close `container` itself.
 *
 * - Focus inside a Mantine portal that `container` is not part of: a
 *   popover's dropdown, a menu, a select's options, a modal opened from
 *   inside — rendered outside `container`, above it (KAN-594).
 * - Focus inside a popup rendered in place in `container`: a `dialog`,
 *   `menu` or `listbox` that is not `container` itself.
 * - Focus on a trigger inside `container` whose popup is open.
 *
 * `container` null (nothing to compare against) reads only the portal rule. */
export function escapeBelongsToAPopupInside(target: EventTarget | null, container: HTMLElement | null): boolean {
  if (!(target instanceof Element)) return false;
  const insideContainer = container !== null && container.contains(target);
  const portal = target.closest('[data-portal="true"]');
  if (portal !== null && !insideContainer && (container === null || !portal.contains(container))) return true;
  if (container === null || !insideContainer) return false;
  const popup = target.closest('[role="dialog"], [role="menu"], [role="listbox"]');
  if (popup !== null && popup !== container && container.contains(popup)) return true;
  const trigger = target.closest(OPEN_POPUP_TRIGGER);
  return trigger !== null && container.contains(trigger);
}

/** KAN-605 — a kit popover panel (views and filters, Columns) closes on
 * Escape **wherever focus is** while it is open, and claims the keypress
 * with `preventDefault()` so an enclosing Overlay stays open. Mantine's
 * Popover closes on Escape only when focus is inside its dropdown; after a
 * click that leaves focus on the trigger (Chrome), nowhere (Safari and
 * Firefox on macOS focus no button on click) or on `<body>` (a click that
 * unmounts the focused control, «Neuer Filter…»), the Escape would otherwise
 * reach the Overlay and close it. An Escape that belongs to a popup opened
 * inside the panel (a condition Select, a modal) is left to that popup. */
export function useEscapeClosesPanel(opened: boolean, close: () => void, dropdownRef: RefObject<HTMLElement | null>) {
  useEffect(() => {
    if (!opened) return;
    let ownedByInnerPopup = false;
    const onKeyDownCapture = (event: KeyboardEvent) => {
      if (event.key === "Escape") ownedByInnerPopup = escapeBelongsToAPopupInside(event.target, dropdownRef.current);
    };
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key !== "Escape" || event.isComposing) return;
      const inner = ownedByInnerPopup;
      ownedByInnerPopup = false;
      if (inner || event.defaultPrevented) return;
      event.preventDefault();
      close();
    };
    document.addEventListener("keydown", onKeyDownCapture, { capture: true });
    document.addEventListener("keydown", onKeyDown);
    return () => {
      document.removeEventListener("keydown", onKeyDownCapture, { capture: true });
      document.removeEventListener("keydown", onKeyDown);
    };
  }, [opened, close, dropdownRef]);
}
