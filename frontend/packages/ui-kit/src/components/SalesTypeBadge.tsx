import { Badge, type BadgeTone } from "./Badge";

/** WP-8 PR-1 — "ART" column on the confirmed "Offerten & Verträge" grid.
 *
 * KAN-334 — the kit maps a value to a tone only. `label` is required: a kit
 * badge never carries a user-visible default string (KAN-110/KAN-181); the
 * app passes the translated label.
 */
export type SalesDealEntityType = "offer" | "contract";

const TONE: Record<SalesDealEntityType, BadgeTone> = {
  offer: "slate",
  contract: "purple",
};

export function SalesTypeBadge({ entityType, label }: { entityType: SalesDealEntityType; label: string }) {
  return (
    <Badge tone={TONE[entityType]} dot>
      {label}
    </Badge>
  );
}
