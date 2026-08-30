import { useState, type ReactNode } from "react";
import { ChevronDown, ChevronUp, Lock } from "lucide-react";
import { purple, radius, slate, spacing } from "../tokens";

export interface ProgressiveDisclosureProps {
  /** e.g. "Internal figures" — never the figures themselves, which only
   * render once expanded. */
  label: string;
  children: ReactNode;
}

/**
 * § Detail Screens — in-card progressive disclosure, for figures a seller
 * sees but a customer-facing view never should (margin, cost, supplier
 * reference). The dashed border and lock badge mark it as "there, but
 * deliberately not shown by default" rather than a plain collapsed
 * section. Open state is NEVER persisted — no preference key, no
 * `useState` initializer reading anything — reopening it every visit is
 * the point: it's a deliberate look, not a remembered one.
 */
export function ProgressiveDisclosure({ label, children }: ProgressiveDisclosureProps) {
  const [open, setOpen] = useState(false);

  return (
    <div style={{ border: `1px dashed ${slate[3]}`, borderRadius: radius.md, padding: spacing.sm }}>
      <button
        type="button"
        onClick={() => setOpen((o) => !o)}
        aria-expanded={open}
        style={{
          display: "flex",
          alignItems: "center",
          gap: spacing.xs,
          width: "100%",
          border: "none",
          background: "none",
          cursor: "pointer",
          padding: 0,
          fontSize: 13,
          fontWeight: 600,
          color: purple[7],
        }}
      >
        <Lock size={14} aria-hidden="true" />
        <span>{label}</span>
        {open ? <ChevronUp size={14} style={{ marginLeft: "auto" }} /> : <ChevronDown size={14} style={{ marginLeft: "auto" }} />}
      </button>
      {open && <div style={{ marginTop: spacing.sm }}>{children}</div>}
    </div>
  );
}
