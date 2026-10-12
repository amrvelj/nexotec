import { Badge, type BadgeTone } from "./Badge";

/** WP-8 PR-9 — a manual figure is "marked manual everywhere it renders"
 * (ADR-066 as amended) — this badge is that one marking, reused on the
 * list, the detail card, and anywhere else a valuation's value appears.
 *
 * KAN-110 — the kit maps a source to a tone only. `label` is required: a
 * kit badge never carries a user-visible default string, the app passes
 * the translated label (ValuationSourceMarker is the one place that does).
 */
export type ValuationSource = "auto_i_dat" | "manual";

const TONE: Record<ValuationSource, BadgeTone> = {
  auto_i_dat: "informational",
  manual: "warning",
};

export function ValuationSourceBadge({ source, label }: { source: ValuationSource; label: string }) {
  return <Badge tone={TONE[source]}>{label}</Badge>;
}
