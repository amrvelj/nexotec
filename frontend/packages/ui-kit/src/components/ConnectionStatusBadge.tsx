import { Badge, type BadgeTone } from "./Badge";

/** WP-6 PR-7 — app.integration.models.connection.ConnectionStatus, exact
 * mirror of ValuationStatusBadge's own Record<Enum,{tone,label}> shape.
 */
export type ConnectionStatus = "connected" | "not_configured" | "error" | "expired" | "disabled";

const CONFIG: Record<ConnectionStatus, { tone: BadgeTone; label: string }> = {
  connected: { tone: "success", label: "Verbunden" },
  not_configured: { tone: "slate", label: "Nicht konfiguriert" },
  error: { tone: "destructive", label: "Fehler" },
  expired: { tone: "warning", label: "Abgelaufen" },
  disabled: { tone: "slate", label: "Deaktiviert" },
};

export function ConnectionStatusBadge({ status, label }: { status: ConnectionStatus; label?: string }) {
  const config = CONFIG[status];
  return (
    <Badge tone={config.tone} dot>
      {label ?? config.label}
    </Badge>
  );
}
