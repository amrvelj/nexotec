// @vitest-environment jsdom
import { describe, expect, it, vi } from "vitest";
import { render, screen } from "@testing-library/react";
import { DataGrid } from "./DataGrid";
import type { Density, GridColumnDef } from "./types";
import { FR_DATA_GRID_LABELS } from "../test/frenchLabels";

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

// KAN-573 — § Composite cells: at `default` density the primary and the
// secondary share one line. The primary is the column's fact: it must keep
// its width while the secondary still has any, so the secondary shrinks to
// an ellipsis first. Before the fix the secondary had `flex-shrink: 0`, and
// a 17-character VIN cut the stock grid's vehicle name to «V…».
//
// jsdom computes no widths, so the test pins the flex contract that decides
// who gives way; the 1440 px measurement in the browser is the ticket's
// screenshot.

interface Car {
  id: string;
  name: string;
  vin: string;
}

const columns: GridColumnDef<Car>[] = [
  {
    id: "vehicle",
    header: "Véhicule",
    cell: ({ row }) => row.original.name,
    meta: { secondary: (row) => row.vin },
  },
];

function grid(density: Density) {
  return (
    <DataGrid<Car>
      columns={columns}
      rows={[{ id: "a", name: "Volkswagen Golf 1.5 eTSI Style", vin: "WVWZZZCDZMW055401" }]}
      getRowId={(r) => r.id}
      sort={[]}
      onSortChange={() => {}}
      density={density}
      loading={false}
      fetchingNextPage={false}
      hasNextPage={false}
      onLoadMore={() => {}}
      total={1}
      totalIsEstimate={false}
      isFiltered={false}
      labels={FR_DATA_GRID_LABELS}
      emptyState={{ icon: null, title: "", description: "" }}
    />
  );
}

describe("DataGrid composite cell at default density (KAN-573)", () => {
  it("keeps the primary whole and lets the secondary shrink first", () => {
    render(grid("default"));
    const primary = screen.getByText("Volkswagen Golf 1.5 eTSI Style");
    const secondary = screen.getByText("WVWZZZCDZMW055401");
    expect(primary.dataset.compositePart).toBe("primary");
    expect(secondary.dataset.compositePart).toBe("secondary");
    // one line, both facts
    expect(primary.parentElement).toBe(secondary.parentElement);
    expect(primary.parentElement?.style.display).toBe("flex");

    // the primary never shrinks; it is capped at the cell, where it ellipsises
    expect(primary.style.flexShrink).toBe("0");
    expect(primary.style.maxWidth).toBe("100%");
    expect(primary.style.textOverflow).toBe("ellipsis");

    // the secondary gives way, down to nothing
    expect(secondary.style.flexShrink).toBe("1");
    expect(secondary.style.minWidth).toBe("0px");
    expect(secondary.style.overflow).toBe("hidden");
    expect(secondary.style.textOverflow).toBe("ellipsis");
  });

  it("leaves comfortable (stacked) and compact (no secondary) as they are", () => {
    const { container, unmount } = render(grid("comfortable"));
    // stacked: two block lines, not the one-line composite
    expect(container.querySelector('[data-composite="inline"]')).toBeNull();
    expect(screen.getByText("Volkswagen Golf 1.5 eTSI Style").tagName).toBe("DIV");
    expect(screen.getByText("WVWZZZCDZMW055401").tagName).toBe("DIV");
    unmount();
    render(grid("compact"));
    expect(screen.getByText("Volkswagen Golf 1.5 eTSI Style")).toBeTruthy();
    expect(screen.queryByText("WVWZZZCDZMW055401")).toBeNull();
  });
});
