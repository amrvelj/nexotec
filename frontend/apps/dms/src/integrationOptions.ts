import type { ConnectionStatus } from '@nexotec/ui-kit'

type Translate = (key: string) => string

// KAN-181 — the kit's ConnectionStatusBadge carries tones only; the label is
// the app's translation, looked up by the raw status string the integration
// registry stores (app.integration.models.connection.ConnectionStatus).
export function translatedConnectionStatusLabel(t: Translate, status: ConnectionStatus): string {
  return t(`integrationEnums.connectionStatus.${status}`)
}
