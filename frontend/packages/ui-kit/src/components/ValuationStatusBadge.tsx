import { Badge, type BadgeTone } from "./Badge";

/** WP-8 PR-9 — derived on read, never stored (app.valuation.services.
 * valuation::derive_status): "draft" | "valid" | "expired" | "used".
 * "Läuft ab" (expiring soon) is a LIST CHIP predicate on top of "valid",
 * never its own derived status — a valuation is never simultaneously
 * "valid" and "expiring_soon" as far as this badge is concerned, since
 * the backend's own derive_status has exactly four values.
 */
export type ValuationStatus = "draft" | "valid" | "expired" | "used";

const CONFIG: Record<ValuationStatus, { tone: BadgeTone; label: string }> = {
  draft: { tone: "slate", label: "Entwurf" },
  valid: { tone: "success", label: "Gültig" },
  expired: { tone: "destructive", label: "Abgelaufen" },
  used: { tone: "purple", label: "Verwendet" },
};

export function ValuationStatusBadge({ status, label }: { status: ValuationStatus; label?: string }) {
  const config = CONFIG[status];
  return (
    <Badge tone={config.tone} dot>
      {label ?? config.label}
    </Badge>
  );
}
