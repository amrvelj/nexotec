// @vitest-environment jsdom
import { describe, expect, it } from "vitest";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { getDefaultZIndex, MantineProvider } from "@mantine/core";
import { useEffect } from "react";
import { testTheme } from "../test/testTheme";
import { FR_VIEWS_AND_FILTERS_LABELS } from "../test/frenchLabels";
import { ViewsAndFilters } from "../datagrid/ViewsAndFilters";
import { zIndex } from "../tokens";
import { OverlayProvider, useOverlay } from "./Overlay";

// KAN-594 — the Overlay (ADR-059) sat at z-index 1000, above every Mantine
// layer, so a popover a grid opened from inside it (the views-and-filters
// control in the configurator's catalogue browse) rendered in Mantine's
// portal *behind* the overlay and could not be used. The kit now settles the
// order once (`zIndex.overlay`, below Mantine's modal and popover layers).

function zOf(el: HTMLElement): number {
  return Number(el.style.zIndex);
}

function OpensOnMount() {
  const overlay = useOverlay();
  useEffect(() => {
    overlay.push({
      key: "browse",
      content: (
        <ViewsAndFilters
          currentViewName="Tous les véhicules"
          views={[{ id: "v1", name: "Mes véhicules", snapshot: {} }]}
          onApplyView={() => {}}
          onSaveCurrentAsView={() => {}}
          onDeleteView={() => {}}
          onSetDefaultView={() => {}}
          fields={[{ id: "canton", label: "Canton", type: "text" }]}
          predicates={[]}
          onPredicatesChange={() => {}}
          onResetFilters={() => {}}
          labels={FR_VIEWS_AND_FILTERS_LABELS}
        />
      ),
    });
    // Push once, on mount.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);
  return null;
}

describe("Overlay — what its content opens in Mantine's portal paints above it (KAN-594)", () => {
  it("a grid's views-and-filters popover opened inside an overlay sits above the overlay layer", async () => {
    const user = userEvent.setup();
    render(
      <MantineProvider theme={testTheme}>
        <OverlayProvider closeLabel="Fermer">
          <OpensOnMount />
        </OverlayProvider>
      </MantineProvider>,
    );
    const layer = (await screen.findByRole("button", { name: "Fermer" })).closest('[aria-modal="true"]') as HTMLElement;
    await user.click(screen.getByRole("button", { name: /Tous les véhicules/ }));
    const panel = await screen.findByText(FR_VIEWS_AND_FILTERS_LABELS.viewsHeading);
    // The popover's dropdown is portalled out of the overlay layer: it is not
    // the layer's descendant, so only z-index decides which one paints on top.
    const dropdown = panel.closest(".mantine-Popover-dropdown") as HTMLElement;
    expect(dropdown).not.toBeNull();
    expect(layer.contains(dropdown)).toBe(false);
    expect(zOf(dropdown)).toBeGreaterThan(zOf(layer));
  });

  it("the overlay layer sits above the app shell and below Mantine's modal and popover layers", () => {
    expect(zIndex.overlay).toBeGreaterThan(getDefaultZIndex("app"));
    expect(zIndex.overlay).toBeLessThan(getDefaultZIndex("modal"));
    expect(zIndex.overlay).toBeLessThan(getDefaultZIndex("popover"));
    expect(zIndex.stickyBar).toBeLessThan(zIndex.overlay);
  });
});
