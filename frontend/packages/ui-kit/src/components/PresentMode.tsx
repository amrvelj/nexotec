import { createContext, useContext, useEffect, type ReactNode } from "react";
import { X } from "lucide-react";
import { radius, shadow, slate, spacing, white } from "../tokens";

const PresentModeContext = createContext(false);

/**
 * True while the nearest ancestor is a `<PresentMode>` — the signal
 * `PresentModeHidden` (and any bespoke check a screen needs) reads to
 * decide whether cost/margin/supplier/internal-reference content should
 * exist on screen at all right now.
 */
export function usePresentMode(): boolean {
  return useContext(PresentModeContext);
}

export interface PresentModeProps {
  children: ReactNode;
  onExit: () => void;
  exitLabel?: string;
}

/**
 * § Component Contracts — Present mode. Full-screen, customer-facing,
 * carries no cost/margin/supplier/internal reference. "A PRESENTATION
 * boundary, not a permission one" — it hides content structurally for
 * EVERY viewer regardless of role, because the thing being protected is
 * what's literally on the screen a customer might be looking at (a
 * shared monitor, a screen-share), not who is allowed to see it. A seller
 * with full commercial visibility (ADR-049) still sees nothing sensitive
 * here — this is not a permissions check, and must never become one.
 */
export function PresentMode({ children, onExit, exitLabel = "Exit present mode" }: PresentModeProps) {
  useEffect(() => {
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === "Escape") onExit();
    };
    window.addEventListener("keydown", onKeyDown);
    return () => window.removeEventListener("keydown", onKeyDown);
  }, [onExit]);

  return (
    <PresentModeContext.Provider value={true}>
      <div
        role="dialog"
        aria-modal="true"
        style={{
          position: "fixed",
          inset: 0,
          zIndex: 2000,
          backgroundColor: white,
          overflow: "auto",
        }}
      >
        <button
          type="button"
          onClick={onExit}
          aria-label={exitLabel}
          style={{
            position: "fixed",
            top: spacing.md,
            right: spacing.md,
            zIndex: 2001,
            display: "flex",
            alignItems: "center",
            gap: 6,
            border: `1px solid ${slate[2]}`,
            borderRadius: radius.full,
            padding: `6px ${spacing.md}`,
            background: white,
            color: slate[6],
            fontSize: 13,
            fontWeight: 600,
            cursor: "pointer",
            boxShadow: shadow.md,
          }}
        >
          <X size={14} />
          {exitLabel}
        </button>
        {children}
      </div>
    </PresentModeContext.Provider>
  );
}

/**
 * The structural half of the boundary: wrap any cost/margin/supplier/
 * internal-reference content in this, once, at the point it's rendered.
 * Renders nothing at all while presenting — not dimmed, not blurred, not
 * present in the DOM for a screen-reader or a screen-share to pick up.
 */
export function PresentModeHidden({ children }: { children: ReactNode }) {
  const presenting = usePresentMode();
  if (presenting) return null;
  return <>{children}</>;
}
