import { Badge, type BadgeTone } from "./Badge";

/** WP-6 PR-7 — app.integration.models.connection.ConnectionStatus.
 *
 * KAN-181 — the kit maps a status to a tone only. `label` is required: a
 * kit badge never carries a user-visible default string; the app passes the
 * translated label (`integrationEnums.connectionStatus.*` via
 * translatedConnectionStatusLabel).
 */
export type ConnectionStatus = "connected" | "not_configured" | "error" | "expired" | "disabled";

const TONE: Record<ConnectionStatus, BadgeTone> = {
  connected: "success",
  not_configured: "slate",
  error: "destructive",
  expired: "warning",
  disabled: "slate",
};

export function ConnectionStatusBadge({ status, label }: { status: ConnectionStatus; label: string }) {
  return (
    <Badge tone={TONE[status]} dot>
      {label}
    </Badge>
  );
}
