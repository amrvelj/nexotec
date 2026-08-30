import { Badge, type BadgeTone } from "./Badge";

/** Independent of StockLifecycleStatus (ADR-054) — this badge only ever
 * says whether the item is held against a contract, never where it is in
 * the pipeline/in-stock axis.
 */
export type StockReservationState = "none" | "reserved";

const CONFIG: Record<StockReservationState, { tone: BadgeTone; label: string }> = {
  none: { tone: "slate", label: "Free" },
  reserved: { tone: "warning", label: "Reserved" },
};

export function StockReservationBadge({ state, label }: { state: StockReservationState; label?: string }) {
  const config = CONFIG[state];
  return (
    <Badge tone={config.tone} dot>
      {label ?? config.label}
    </Badge>
  );
}
