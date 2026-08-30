import { Badge, type BadgeTone } from "./Badge";

/** WP-8 PR-9 — a manual figure is "marked manual everywhere it renders"
 * (ADR-066 as amended) — this badge is that one marking, reused on the
 * list, the detail card, and anywhere else a valuation's value appears.
 */
export type ValuationSource = "auto_i_dat" | "manual";

const CONFIG: Record<ValuationSource, { tone: BadgeTone; label: string }> = {
  auto_i_dat: { tone: "informational", label: "auto-i-dat" },
  manual: { tone: "warning", label: "Manuell" },
};

export function ValuationSourceBadge({ source, label }: { source: ValuationSource; label?: string }) {
  const config = CONFIG[source];
  return <Badge tone={config.tone}>{label ?? config.label}</Badge>;
}
