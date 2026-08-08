import { createContext, useContext, useEffect } from "react";

const BreadcrumbContext = createContext<(segments: string[]) => void>(() => {});

export const BreadcrumbProvider = BreadcrumbContext.Provider;

/**
 * A page calls this with its own breadcrumb ("Group / Entity / Record" per
 * § Topbar) so Topbar can render it without AppShell needing to know
 * anything about individual routes. Re-registers whenever segments change
 * (e.g. once a customer's name has loaded, replacing "New customer").
 */
export function useSetBreadcrumb(segments: string[]): void {
  const setBreadcrumb = useContext(BreadcrumbContext);
  // eslint-disable-next-line react-hooks/exhaustive-deps
  useEffect(() => setBreadcrumb(segments), [setBreadcrumb, JSON.stringify(segments)]);
}
