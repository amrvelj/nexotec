/// <reference types="vite/client" />

interface ImportMetaEnv {
  readonly VITE_API_BASE_URL?: string
  /** "true" on a deployment that runs no outbox worker (render.yaml's
   * staging web service) — see src/config/deployment.ts (KAN-275). */
  readonly VITE_OUTBOX_WORKER_ABSENT?: string
}

interface ImportMeta {
  readonly env: ImportMetaEnv
}
