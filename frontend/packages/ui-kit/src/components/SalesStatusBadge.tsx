import { Badge, type BadgeTone } from "./Badge";

/** WP-8 PR-1 — the confirmed "Offerten & Verträge" grid has ONE shared
 * STATUS column spanning two independent vocabularies depending on ART
 * (offer: draft/open/cancelled; contract: pending/confirmed/cancelled/
 * invoiced) — never two columns, never a merged enum on the entity side
 * (app.sales.models.deal.SalesDeal.status is a bare string for exactly
 * this reason). This badge is the single place both vocabularies map to a
 * tone, so the two surfaces (grid + detail headers, once built) can never
 * render a status with two different colours.
 *
 * KAN-334 — the kit maps a value to a tone only. `label` is required: a kit
 * badge never carries a user-visible default string (KAN-110/KAN-181); the
 * app passes the translated label.
 */
export type SalesDealStatus = "draft" | "open" | "pending" | "confirmed" | "cancelled" | "invoiced";

const TONE: Record<SalesDealStatus, BadgeTone> = {
  draft: "slate",
  // "open" is deliberately shared: an OFFER's "Offen" and a CONTRACT's
  // "Offen" (pending) are the confirmed reference prototype's own two
  // distinct meanings for the identical German word — the grid renders
  // them with the same tone since a reader disambiguates via the ART
  // column right next to it, not via colour.
  open: "informational",
  pending: "informational",
  confirmed: "success",
  cancelled: "destructive",
  invoiced: "purple",
};

export function SalesStatusBadge({ status, label }: { status: SalesDealStatus; label: string }) {
  return (
    <Badge tone={TONE[status]} dot>
      {label}
    </Badge>
  );
}
