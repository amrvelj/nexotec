import { Badge, type BadgeTone } from "./Badge";

/** WP-8 PR-1 — "ART" column on the confirmed "Offerten & Verträge" grid. */
export type SalesDealEntityType = "offer" | "contract";

const CONFIG: Record<SalesDealEntityType, { tone: BadgeTone; label: string }> = {
  offer: { tone: "slate", label: "Offerte" },
  contract: { tone: "purple", label: "Vertrag" },
};

export function SalesTypeBadge({ entityType, label }: { entityType: SalesDealEntityType; label?: string }) {
  const config = CONFIG[entityType];
  return (
    <Badge tone={config.tone} dot>
      {label ?? config.label}
    </Badge>
  );
}
