import { Badge, type BadgeTone } from "./Badge";

export type StockItemCondition = "new" | "used" | "demo" | "tagesz";

/** Tones match the reference prototype's own dot colours for this exact
 * enum: Occasion (used) grey, Neu (new) purple, Tageszulassung/
 * Vorführwagen (tagesz/demo) blue.
 */
const CONFIG: Record<StockItemCondition, { tone: BadgeTone; label: string }> = {
  used: { tone: "slate", label: "Occasion" },
  new: { tone: "purple", label: "New" },
  tagesz: { tone: "informational", label: "Tageszulassung" },
  demo: { tone: "informational", label: "Vorführwagen" },
};

export function StockConditionBadge({ condition, label }: { condition: StockItemCondition; label?: string }) {
  const config = CONFIG[condition];
  return (
    <Badge tone={config.tone} dot>
      {label ?? config.label}
    </Badge>
  );
}
