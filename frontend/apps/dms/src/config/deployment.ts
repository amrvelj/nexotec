/**
 * Facts about THIS DEPLOYMENT that the UI has to say out loud.
 *
 * `outboxWorkerAbsent()` — KAN-275, documenting ADR-081 (option D): Render's
 * staging runs no outbox worker, so no event consumer and no nightly job
 * runs there (`app/worker.py::register_handlers` and `register_daily_jobs`
 * are the lists). A tester who confirms a contract on staging and sees no
 * pipeline item would otherwise read a configuration as a bug, so the shell
 * shows a persistent notice on every signed-in screen (ADR-084).
 *
 * Driven by an explicit setting, never by the environment's name: Vite
 * inlines `VITE_OUTBOX_WORKER_ABSENT` at build time, and `render.yaml`'s web
 * service sets it in its buildCommand. Only the exact string "true" switches
 * the notice on — unset (docker-compose, CI, `npm run dev`) means off. Read
 * at call time rather than at module load so a render test can stub both
 * states with `vi.stubEnv`.
 */
export function outboxWorkerAbsent(): boolean {
  return import.meta.env.VITE_OUTBOX_WORKER_ABSENT === 'true'
}
