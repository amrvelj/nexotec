import { Badge, type BadgeTone } from "./Badge";

/** WP-8 PR-9 — derived on read, never stored (app.valuation.services.
 * valuation::derive_status): "draft" | "valid" | "expired" | "used".
 * "Läuft ab" (expiring soon) is a LIST CHIP predicate on top of "valid",
 * never its own derived status — a valuation is never simultaneously
 * "valid" and "expiring_soon" as far as this badge is concerned, since
 * the backend's own derive_status has exactly four values.
 *
 * KAN-334 — the kit maps a value to a tone only. `label` is required: a kit
 * badge never carries a user-visible default string (KAN-110/KAN-181); the
 * app passes the translated label.
 */
export type ValuationStatus = "draft" | "valid" | "expired" | "used";

const TONE: Record<ValuationStatus, BadgeTone> = {
  draft: "slate",
  valid: "success",
  expired: "destructive",
  used: "purple",
};

export function ValuationStatusBadge({ status, label }: { status: ValuationStatus; label: string }) {
  return (
    <Badge tone={TONE[status]} dot>
      {label}
    </Badge>
  );
}
