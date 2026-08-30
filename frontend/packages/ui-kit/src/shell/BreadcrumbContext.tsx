import { createContext, useContext, useEffect } from "react";

const BreadcrumbContext = createContext<(segments: string[]) => void>(() => {});

export const BreadcrumbProvider = BreadcrumbContext.Provider;

/**
 * A page calls this with its own breadcrumb ("Group / Entity / Record" per
 * § Topbar) so Topbar can render it without AppShell needing to know
 * anything about individual routes. Re-registers whenever segments change
 * (e.g. once a customer's name has loaded, replacing "New customer").
 *
 * `null` means "don't touch the breadcrumb at all" — for a screen that's
 * shared between a real route and ADR-059 overlay content (`Overlay.tsx`
 * renders inside the same `BreadcrumbProvider` the host screen is in, so
 * an overlaid Customer 360 calling this with its own segments would
 * silently overwrite the host screen's breadcrumb). Passed as an argument
 * rather than skipping the hook call itself, which the Rules of Hooks
 * don't allow — the caller can't conditionally call a hook based on
 * whether it's embedded.
 */
export function useSetBreadcrumb(segments: string[] | null): void {
  const setBreadcrumb = useContext(BreadcrumbContext);
  const key = segments === null ? null : JSON.stringify(segments);
  useEffect(() => {
    if (segments === null) return;
    setBreadcrumb(segments);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [setBreadcrumb, key]);
}
