import { Badge, type BadgeTone } from "./Badge";

export type StockItemCondition = "new" | "used" | "demo" | "tagesz";

/** Tones match the reference prototype's own dot colours for this exact
 * enum: Occasion (used) grey, Neu (new) purple, Tageszulassung/
 * Vorführwagen (tagesz/demo) blue.
 *
 * KAN-334 — the kit maps a value to a tone only. `label` is required: a kit
 * badge never carries a user-visible default string (KAN-110/KAN-181); the
 * app passes the translated label.
 */
const TONE: Record<StockItemCondition, BadgeTone> = {
  used: "slate",
  new: "purple",
  tagesz: "informational",
  demo: "informational",
};

export function StockConditionBadge({ condition, label }: { condition: StockItemCondition; label: string }) {
  return (
    <Badge tone={TONE[condition]} dot>
      {label}
    </Badge>
  );
}
