import { createContext, useCallback, useContext, useEffect, useState, type ReactNode } from "react";
import { X } from "lucide-react";
import { radius, shadow, slate, white } from "../tokens";
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
export function OverlayProvider({ children }: { children: ReactNode }) {
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
  // mid-way through too.
  useEffect(() => {
    if (stack.length === 0) return;
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === "Escape") pop();
    };
    window.addEventListener("keydown", onKeyDown);
    return () => window.removeEventListener("keydown", onKeyDown);
  }, [stack.length, pop]);

  return (
    <OverlayContext.Provider value={{ stack, push, pop, closeAll }}>
      {children}
      {stack.map((entry, index) => (
        <div
          key={entry.key}
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
            zIndex: 1000 + index,
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
            aria-label="Close"
            style={{
              position: "fixed",
              top: 16,
              right: 16,
              zIndex: 1001 + index,
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
