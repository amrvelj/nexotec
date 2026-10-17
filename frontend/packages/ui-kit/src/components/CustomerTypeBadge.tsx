import { Building2, User } from "lucide-react";
import { Badge } from "./Badge";

export type CustomerType = "individual" | "business";

/**
 * "Type badges everywhere. Individual vs business is visually distinct at
 * a glance in every list and header" — and the exact mapping from
 * § Badges and Status: individual = purple + User icon, business =
 * informational blue + Building2 icon.
 *
 * KAN-334 — the kit maps a value to a tone only. `label` is required: a kit
 * badge never carries a user-visible default string (KAN-110/KAN-181); the
 * app passes the translated label.
 */
export function CustomerTypeBadge({ type, label }: { type: CustomerType; label: string }) {
  if (type === "business") {
    return (
      <Badge tone="informational" icon={<Building2 size={14} />}>
        {label}
      </Badge>
    );
  }
  return (
    <Badge tone="purple" icon={<User size={14} />}>
      {label}
    </Badge>
  );
}
