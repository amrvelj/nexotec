// @vitest-environment jsdom
import { describe, expect, it, vi } from "vitest";
import { render, screen } from "@testing-library/react";
import { MantineProvider } from "@mantine/core";
import { testTheme } from "../test/testTheme";
import { DataGrid } from "./DataGrid";
import type { GridColumnDef } from "./types";
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

// KAN-591 — the row's checkbox rendered in the generic Cell, which pads
// `spacing.sm spacing.md` and clips with `text-overflow: ellipsis`. In the
// 40 px column the padding plus the checkbox overflowed it, and the browser
// drew a stray «…» beside every row's checkbox. The body's select cell now
// lays out like the header's: centred, no padding, no ellipsis.

interface Row {
  id: string;
}

const columns: GridColumnDef<Row>[] = [{ id: "id", header: "NUMÉRO", cell: ({ row }) => row.original.id }];

function renderGrid() {
  render(
    <MantineProvider theme={testTheme}>
      <DataGrid<Row>
        columns={columns}
        rows={[{ id: "K-1" }]}
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
        selection={{
          selectedIds: new Set<string>(),
          onSelectionChange: () => {},
          selectAllLabel: "Tout sélectionner",
          selectRowLabel: (row: Row) => `Sélectionner ${row.id}`,
        }}
      />
    </MantineProvider>,
  );
}

describe("DataGrid — the row's selection cell (KAN-591)", () => {
  it("lays out like the header's: centred, no padding, nothing that can draw an ellipsis", () => {
    renderGrid();
    const cell = screen.getByRole("checkbox", { name: "Sélectionner K-1" }).closest('[role="cell"]') as HTMLElement;
    expect(cell.style.padding).toBe("");
    expect(cell.style.textOverflow).toBe("");
    expect(cell.style.whiteSpace).toBe("");
    expect(cell.style.display).toBe("flex");
    expect(cell.style.alignItems).toBe("center");
    expect(cell.style.justifyContent).toBe("center");
    expect(cell.style.width).toBe("40px");
  });

  it("is the header's twin: same width and centring as SelectHeaderCell, still pinned at the left edge", () => {
    renderGrid();
    const header = screen.getByRole("checkbox", { name: "Tout sélectionner" }).closest('[role="columnheader"]') as HTMLElement;
    const cell = screen.getByRole("checkbox", { name: "Sélectionner K-1" }).closest('[role="cell"]') as HTMLElement;
    for (const prop of ["width", "flex", "display", "alignItems", "justifyContent", "position", "left"] as const) {
      expect(cell.style[prop]).toBe(header.style[prop]);
    }
  });
});
