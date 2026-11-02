import { theme } from '@nexotec/ui-kit'

/**
 * KAN-526 — the theme every render test mounts Mantine with. Mantine's
 * `env="test"` renders a transition's children at once, but its
 * `useTransition` hook still runs the transition: two animation frames, then a
 * `setTimeout` for the duration, each ending in a state update. One of those
 * timers fired after its file's jsdom was torn down and threw «window is not
 * defined» (the run exited 1 with every test green). With reduced motion
 * respected — and `src/test/setup.ts`'s `matchMedia` stand-in reporting
 * `prefers-reduced-motion: reduce` — a transition's duration is 0 and the hook
 * switches state synchronously: no frame, no timer, nothing left to land after
 * a test. A render test that mounts its own `MantineProvider` passes this.
 */
export const testTheme = { ...theme, respectReducedMotion: true }
