import { Badge, type BadgeTone } from "./Badge";

/** Independent of StockLifecycleStatus (ADR-054) — this badge only ever
 * says whether the item is held against a contract, never where it is in
 * the pipeline/in-stock axis.
 *
 * KAN-334 — the kit maps a value to a tone only. `label` is required: a kit
 * badge never carries a user-visible default string (KAN-110/KAN-181); the
 * app passes the translated label.
 */
export type StockReservationState = "none" | "reserved";

const TONE: Record<StockReservationState, BadgeTone> = {
  none: "slate",
  reserved: "warning",
};

export function StockReservationBadge({ state, label }: { state: StockReservationState; label: string }) {
  return (
    <Badge tone={TONE[state]} dot>
      {label}
    </Badge>
  );
}
