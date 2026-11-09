import { createContext, useCallback, useContext, useEffect, useRef, useState, type ReactNode } from "react";
import { X } from "lucide-react";
import { radius, shadow, slate, white, zIndex } from "../tokens";
import { closeAllEntries, popEntry, pushEntry } from "./overlayStack";

export interface OverlayEntry {
  /** Unique per push — used as the React key and as the target of a
   * `pop`/`replaceTop` call from outside the stack's own top-of-stack
   * assumption isn't needed for anything today, but the key still has to
   * be stable and unique per open screen. */
  key: string;
  /** The caller's own screen content, rendered as a normal React subtree —
   * NOT a route. It renders because it's a value in this stack, never
   * because a URL changed underneath it. A screen that reads its own id
   * from `useParams()` when navigated to normally needs a second,
   * prop-driven entry point to be usable here; that migration belongs to
   * whichever module first opens it as an overlay, not to this component. */
  content: ReactNode;
  /** § U-11 — fired exactly once, when THIS layer closes (top-of-stack
   * `pop()`, or swept up in `closeAll()`). The caller's chance to
   * invalidate whatever query the screen underneath should now see fresh.
   * Never call this from inside the overlay's own content as a manual
   * refetch trigger — the whole point of a callback here, instead of the
   * underlying screen guessing when to refetch on its own, is that this
   * fires reliably on every close path, including closeAll(). */
  onClose?: () => void;
}

interface OverlayContextValue {
  stack: OverlayEntry[];
  push: (entry: OverlayEntry) => void;
  /** Closes exactly the top layer — the only layer it is ever coherent to
   * close on its own, since anything below it in the stack is, by
   * definition, still covered by something else. */
  pop: () => void;
  closeAll: () => void;
}

const OverlayContext = createContext<OverlayContextValue | null>(null);

/**
 * § ADR-059 — The Overlay. "Opening a record from inside a process renders
 * it as an overlay on top, not a navigation. Losing a half-built offer is
 * a defect, not a trade-off." This is a query-context stack, not URL
 * manipulation — the naive "set the hash, set it back" approach fires the
 * router and destroys the screen underneath, which is the exact bug this
 * component exists to prevent. Every layer below the top stays MOUNTED
 * (`display: none`, not unmounted) while covered, so its own state (a
 * half-filled form two layers down) survives however many screens get
 * opened on top of it.
 */
export function OverlayProvider({
  children,
  closeLabel,
}: {
  children: ReactNode;
  /** The fixed close button's accessible name, translated by the caller —
   * the kit ships no English of its own (KAN-364). */
  closeLabel: string;
}) {
  const [stack, setStack] = useState<OverlayEntry[]>([]);

  const push = useCallback((entry: OverlayEntry) => {
    setStack((current) => pushEntry(current, entry));
  }, []);

  const pop = useCallback(() => {
    setStack((current) => {
      const { next, closed } = popEntry(current);
      closed?.onClose?.();
      return next;
    });
  }, []);

  const closeAll = useCallback(() => {
    setStack((current) => {
      const { closedInOrder } = closeAllEntries(current);
      closedInOrder.forEach((entry) => entry.onClose?.());
      return [];
    });
  }, []);

  // Escape closes the top layer only — never a full closeAll from one
  // keypress, which would silently discard whatever the layers below were
  // mid-way through too. And only when nothing opened inside the layer is
  // open (KAN-605): an Escape that dismisses a popover, dropdown, menu or
  // modal belongs to that, not to the overlay.
  const topLayerRef = useRef<HTMLDivElement | null>(null);
  useEffect(() => {
    if (stack.length === 0) return;
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key !== "Escape" || event.isComposing) return;
      if (escapeBelongsToAPopupInside(event.target, topLayerRef.current)) return;
      pop();
    };
    // Capture phase: the decision reads the popups' state *before* any of
    // them handles this keypress. A Select closes its dropdown in React's
    // keydown handler, which flushes synchronously, so by the time the
    // event bubbled to `window` its input would already read
    // `aria-expanded="false"`.
    window.addEventListener("keydown", onKeyDown, { capture: true });
    return () => window.removeEventListener("keydown", onKeyDown, { capture: true });
  }, [stack.length, pop]);

  return (
    <OverlayContext.Provider value={{ stack, push, pop, closeAll }}>
      {children}
      {stack.map((entry, index) => (
        <div
          key={entry.key}
          ref={index === stack.length - 1 ? topLayerRef : undefined}
          role="dialog"
          aria-modal="true"
          // Every layer stays in the tree; only the top one paints and can
          // receive input — "re-render targets the top layer" in practice
          // means covered layers never repaint just because the stack
          // above them changed, since CSS (not unmounting) is what hides
          // them.
          style={{
            display: index === stack.length - 1 ? "block" : "none",
            position: "fixed",
            inset: 0,
            // Below Mantine's modal and popover layers, so whatever the
            // content opens in Mantine's portal paints above it (KAN-594).
            zIndex: zIndex.overlay + index,
            backgroundColor: white,
            overflow: "auto",
          }}
        >
          {/* Same fixed-corner convention as PresentMode's own exit
              button — content never has to build its own close affordance
              to be usable inside this. */}
          <button
            type="button"
            onClick={pop}
            aria-label={closeLabel}
            style={{
              position: "fixed",
              top: 16,
              right: 16,
              zIndex: zIndex.overlay + index + 1,
              display: "flex",
              alignItems: "center",
              justifyContent: "center",
              width: 32,
              height: 32,
              border: `1px solid ${slate[2]}`,
              borderRadius: radius.full,
              background: white,
              color: slate[6],
              cursor: "pointer",
              boxShadow: shadow.md,
            }}
          >
            <X size={16} />
          </button>
          {entry.content}
        </div>
      ))}
    </OverlayContext.Provider>
  );
}

export function useOverlay(): OverlayContextValue {
  const ctx = useContext(OverlayContext);
  if (!ctx) throw new Error("useOverlay must be used within an OverlayProvider");
  return ctx;
}

/**
 * KAN-605 — whether an Escape keydown is the dismissal of something opened
 * inside the top overlay layer rather than a request to close the layer.
 * Mantine's Popover, Combobox and Modal close themselves on Escape but never
 * stop the event, so it reaches the overlay's window listener too; popping
 * the layer then would discard the configuration half-built in it (ADR-059).
 *
 * - Focus inside Mantine's portal (`data-portal`): a popover's dropdown, a
 *   menu, a modal — they render outside the layer, above it (KAN-594).
 * - Focus inside the layer on a trigger whose popup is open: a Popover's or
 *   Menu's button (`aria-expanded="true"`), a Select's or Autocomplete's
 *   input (Mantine's Combobox marks it `data-expanded="true"` and
 *   `data-mantine-stop-propagation="true"` — the flag Mantine's own Modal
 *   honours before closing on Escape). Read in the capture phase, before
 *   the popup's own handler runs.
 *
 * Anything else — focus in the layer with nothing open, on the page's
 * button that opened the overlay, or nowhere — closes the layer as before.
 */
const OPEN_POPUP_TRIGGER =
  '[aria-expanded="true"], [data-expanded="true"], [data-mantine-stop-propagation="true"]';

function escapeBelongsToAPopupInside(target: EventTarget | null, layer: HTMLElement | null): boolean {
  if (!(target instanceof Element)) return false;
  if (target.closest('[data-portal="true"]')) return true;
  return layer !== null && layer.contains(target) && target.closest(OPEN_POPUP_TRIGGER) !== null;
}
