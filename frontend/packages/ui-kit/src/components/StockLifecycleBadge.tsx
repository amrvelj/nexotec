import { Badge, type BadgeTone } from "./Badge";

/**
 * WP-7 PR-1 (ADR-054). Exactly three values — "sold" is deliberately not
 * one of them: a sold (invoiced) item is absent from the active list
 * (FR-I-12), never a lifecycle value here. Independent of
 * StockReservationState — every combination is legal, including
 * pipeline+reserved (a factory order already sold), so this badge never
 * tries to express reservation on its own.
 *
 * KAN-334 — the kit maps a value to a tone only. `label` is required: a kit
 * badge never carries a user-visible default string (KAN-110/KAN-181); the
 * app passes the translated label.
 */
export type StockLifecycleStatus = "pipeline" | "in_stock" | "storno_pending";

const TONE: Record<StockLifecycleStatus, BadgeTone> = {
  pipeline: "warning",
  in_stock: "success",
  storno_pending: "destructive",
};

export function StockLifecycleBadge({ status, label }: { status: StockLifecycleStatus; label: string }) {
  return (
    <Badge tone={TONE[status]} dot>
      {label}
    </Badge>
  );
}
