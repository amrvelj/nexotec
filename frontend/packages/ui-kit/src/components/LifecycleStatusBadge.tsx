import { Badge, type BadgeTone } from "./Badge";

export type LifecycleStatus = "prospect" | "active" | "inactive" | "merged" | "do_not_contact";

/**
 * Maps directly to § Badges and Status's lifecycle rows: positive =
 * success/green, in-progress = warning/amber, neutral/terminal = slate
 * ("Merged" struck through), blocking/prohibitive = destructive/red.
 * "The status vocabulary is shared" — this is that vocabulary for
 * Customer.lifecycleStatus specifically; other entities reuse the same
 * tones for their own equivalent states rather than inventing new colours.
 *
 * KAN-181 — the kit maps a status to a tone only. `label` is required: a
 * kit badge never carries a user-visible default string; the app passes the
 * translated label (`customerEnums.lifecycleStatus.*` via
 * translatedLifecycleLabel).
 */
const CONFIG: Record<LifecycleStatus, { tone: BadgeTone; strikethrough?: boolean }> = {
  active: { tone: "success" },
  prospect: { tone: "warning" },
  inactive: { tone: "slate" },
  merged: { tone: "slate", strikethrough: true },
  do_not_contact: { tone: "destructive" },
};

export function LifecycleStatusBadge({ status, label }: { status: LifecycleStatus; label: string }) {
  const config = CONFIG[status];
  return (
    <Badge tone={config.tone} dot strikethrough={config.strikethrough}>
      {label}
    </Badge>
  );
}
