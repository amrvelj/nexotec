import { Badge, type BadgeTone } from "./Badge";

/**
 * WP-7 PR-1 (ADR-054). Exactly three values — "sold" is deliberately not
 * one of them: a sold (invoiced) item is absent from the active list
 * (FR-I-12), never a lifecycle value here. Independent of
 * StockReservationState — every combination is legal, including
 * pipeline+reserved (a factory order already sold), so this badge never
 * tries to express reservation on its own.
 */
export type StockLifecycleStatus = "pipeline" | "in_stock" | "storno_pending";

const CONFIG: Record<StockLifecycleStatus, { tone: BadgeTone; label: string }> = {
  pipeline: { tone: "warning", label: "Pipeline" },
  in_stock: { tone: "success", label: "In stock" },
  storno_pending: { tone: "destructive", label: "Storno pending" },
};

export function StockLifecycleBadge({ status, label }: { status: StockLifecycleStatus; label?: string }) {
  const config = CONFIG[status];
  return (
    <Badge tone={config.tone} dot>
      {label ?? config.label}
    </Badge>
  );
}
