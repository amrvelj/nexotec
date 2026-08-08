import { Building2, User } from "lucide-react";
import { Badge } from "./Badge";

export type CustomerType = "individual" | "business";

const LABEL: Record<CustomerType, string> = { individual: "Individual", business: "Business" };

/**
 * "Type badges everywhere. Individual vs business is visually distinct at
 * a glance in every list and header" — and the exact mapping from
 * § Badges and Status: individual = purple + User icon, business =
 * informational blue + Building2 icon.
 */
export function CustomerTypeBadge({ type }: { type: CustomerType }) {
  if (type === "business") {
    return (
      <Badge tone="informational" icon={<Building2 size={14} />}>
        {LABEL.business}
      </Badge>
    );
  }
  return (
    <Badge tone="purple" icon={<User size={14} />}>
      {LABEL.individual}
    </Badge>
  );
}
