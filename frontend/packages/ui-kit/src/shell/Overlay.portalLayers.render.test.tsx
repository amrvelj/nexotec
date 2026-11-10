// @vitest-environment jsdom
import { describe, expect, it, vi } from "vitest";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { getDefaultZIndex, MantineProvider } from "@mantine/core";
import { useEffect } from "react";
import { testTheme } from "../test/testTheme";
import { FR_ACTION_BAR_LABELS, FR_DATA_GRID_LABELS, FR_VIEWS_AND_FILTERS_LABELS } from "../test/frenchLabels";
import { ActionBar } from "../datagrid/ActionBar";
import { DataGrid } from "../datagrid/DataGrid";
import type { GridColumnDef } from "../datagrid/types";
import { ViewsAndFilters } from "../datagrid/ViewsAndFilters";
import { zIndex } from "../tokens";
import { OverlayProvider, useOverlay } from "./Overlay";

// jsdom has no layout (see DataGrid.rowActivate.render.test.tsx): render
// every row instead of letting TanStack Virtual window down to none.
vi.mock("@tanstack/react-virtual", () => ({
  useVirtualizer: ({ count, estimateSize }: { count: number; estimateSize: () => number }) => {
    const size = estimateSize();
    return {
      getVirtualItems: () =>
        Array.from({ length: count }, (_, index) => ({ index, key: index, start: index * size, size })),
      getTotalSize: () => count * size,
      measure: () => {},
    };
  },
}));

// KAN-594 — the Overlay (ADR-059) sat at z-index 1000, above every Mantine
// layer, so a popover a grid opened from inside it (the views-and-filters
// control in the configurator's catalogue browse) rendered in Mantine's
// portal *behind* the overlay and could not be used. The kit now settles the
// order once (`zIndex.overlay`, below Mantine's modal and popover layers).

function zOf(el: HTMLElement): number {
  return Number(el.style.zIndex);
}

interface Row {
  id: string;
}

const columns: GridColumnDef<Row>[] = [{ id: "id", header: "VARIANTE", cell: ({ row }) => row.original.id }];

/** A grid as a screen composes it (`CatalogueBrowseGrid`): the Action Bar
 * carrying the views-and-filters control, then the DataGrid with row menus. */
function BrowseGrid() {
  return (
    <>
      <ActionBar
        searchValue=""
        onSearchChange={() => {}}
        searchPlaceholder="Rechercher…"
        density="default"
        onDensityChange={() => {}}
        onRefresh={() => {}}
        labels={FR_ACTION_BAR_LABELS}
        filterSlot={
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
        }
      />
      <DataGrid<Row>
        columns={columns}
        rows={[{ id: "Golf GTI" }]}
        getRowId={(r) => r.id}
        sort={[]}
        onSortChange={() => {}}
        density="default"
        loading={false}
        fetchingNextPage={false}
        hasNextPage={false}
        onLoadMore={() => {}}
        total={1}
        totalIsEstimate={false}
        isFiltered={false}
        labels={FR_DATA_GRID_LABELS}
        emptyState={{ icon: null, title: "", description: "" }}
        rowActions={() => ({ navigate: [{ label: "Ouvrir la variante", icon: null, onClick: () => {} }] })}
      />
    </>
  );
}

function OpensOnMount() {
  const overlay = useOverlay();
  useEffect(() => {
    overlay.push({ key: "browse", content: <BrowseGrid /> });
    // Push once, on mount.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);
  return null;
}

async function openOverlayWithGrid() {
  render(
    <MantineProvider theme={testTheme}>
      <OverlayProvider closeLabel="Fermer">
        <OpensOnMount />
      </OverlayProvider>
    </MantineProvider>,
  );
  return (await screen.findByRole("button", { name: "Fermer" })).closest('[aria-modal="true"]') as HTMLElement;
}

describe("Overlay — what its content opens in Mantine's portal paints above it (KAN-594)", () => {
  it("a grid's views-and-filters popover opened inside an overlay sits above the overlay layer", async () => {
    const user = userEvent.setup();
    const layer = await openOverlayWithGrid();
    await user.click(screen.getByRole("button", { name: /Tous les véhicules/ }));
    const panel = await screen.findByText(FR_VIEWS_AND_FILTERS_LABELS.viewsHeading);
    // The popover's dropdown is portalled out of the overlay layer: it is not
    // the layer's descendant, so only z-index decides which one paints on top.
    const dropdown = panel.closest(".mantine-Popover-dropdown") as HTMLElement;
    expect(dropdown).not.toBeNull();
    expect(layer.contains(dropdown)).toBe(false);
    expect(zOf(dropdown)).toBeGreaterThan(zOf(layer));
  });

  it("the grid's row menu opened inside an overlay sits above the overlay layer", async () => {
    const user = userEvent.setup();
    const layer = await openOverlayWithGrid();
    await user.click(screen.getByRole("button", { name: FR_DATA_GRID_LABELS.rowActionsLabel }));
    const item = await screen.findByRole("menuitem", { name: "Ouvrir la variante" });
    const menu = item.closest(".mantine-Menu-dropdown") as HTMLElement;
    expect(menu).not.toBeNull();
    expect(layer.contains(menu)).toBe(false);
    expect(zOf(menu)).toBeGreaterThan(zOf(layer));
  });

  it("the overlay layer sits above the app shell and below Mantine's modal and popover layers", () => {
    expect(zIndex.overlay).toBeGreaterThan(getDefaultZIndex("app"));
    expect(zIndex.overlay).toBeLessThan(getDefaultZIndex("modal"));
    expect(zIndex.overlay).toBeLessThan(getDefaultZIndex("popover"));
    expect(zIndex.stickyBar).toBeLessThan(zIndex.overlay);
  });
});
