import { Badge, type BadgeTone } from "./Badge";

/** KAN-810 — the workshop order's state (Aftersales slice 1, table 3.1):
 * "draft" | "accepted" | "ready_for_billing" | "cancelled". The kit maps a
 * value to a tone only; `label` is required — a kit badge never carries a
 * user-visible default string (KAN-110/KAN-181); the app passes the
 * translated label. */
export type ServiceOrderState = "draft" | "accepted" | "ready_for_billing" | "cancelled";

const TONE: Record<ServiceOrderState, BadgeTone> = {
  draft: "slate",
  accepted: "success",
  ready_for_billing: "informational",
  cancelled: "destructive",
};

export function ServiceOrderStateBadge({ state, label }: { state: ServiceOrderState; label: string }) {
  return (
    <Badge tone={TONE[state]} dot>
      {label}
    </Badge>
  );
}
