import { Badge, type BadgeTone } from "./Badge";

export type LifecycleStatus = "prospect" | "active" | "inactive" | "merged" | "do_not_contact";

/**
 * Maps directly to § Badges and Status's lifecycle rows: positive =
 * success/green, in-progress = warning/amber, neutral/terminal = slate
 * ("Merged" struck through), blocking/prohibitive = destructive/red.
 * "The status vocabulary is shared" — this is that vocabulary for
 * Customer.lifecycleStatus specifically; other entities reuse the same
 * tones for their own equivalent states rather than inventing new colours.
 */
const CONFIG: Record<LifecycleStatus, { tone: BadgeTone; label: string; strikethrough?: boolean }> = {
  active: { tone: "success", label: "Active" },
  prospect: { tone: "warning", label: "Prospect" },
  inactive: { tone: "slate", label: "Inactive" },
  merged: { tone: "slate", label: "Merged", strikethrough: true },
  do_not_contact: { tone: "destructive", label: "Do not contact" },
};

export function LifecycleStatusBadge({ status, label }: { status: LifecycleStatus; label?: string }) {
  const config = CONFIG[status];
  return (
    <Badge tone={config.tone} dot strikethrough={config.strikethrough}>
      {label ?? config.label}
    </Badge>
  );
}
