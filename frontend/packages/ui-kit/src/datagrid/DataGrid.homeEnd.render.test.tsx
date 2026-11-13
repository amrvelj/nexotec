// @vitest-environment jsdom
import { describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen } from "@testing-library/react";
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

// KAN-516 review — Home/End jump the grid to its first/last column, but not
// while the caret is in a field inside a cell (an inline edit): there they
// move the caret, as in any input.

interface Row {
  id: string;
}

const columns: GridColumnDef<Row>[] = [
  { id: "id", header: "CODE", cell: ({ row }) => <span data-testid={`text-${row.original.id}`}>{row.original.id}</span> },
  { id: "edit", header: "EDIT", cell: ({ row }) => <input aria-label={`edit-${row.original.id}`} defaultValue={row.original.id} /> },
];

function renderGrid() {
  render(
    <MantineProvider theme={testTheme}>
      <DataGrid<Row>
        columns={columns}
        rows={[{ id: "diesel" }]}
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
      />
    </MantineProvider>,
  );
}

describe("DataGrid — Home/End", () => {
  it("leave an input inside a cell alone", () => {
    renderGrid();
    const input = screen.getByLabelText("edit-diesel");
    // fireEvent returns false when a handler called preventDefault().
    expect(fireEvent.keyDown(input, { key: "Home" })).toBe(true);
    expect(fireEvent.keyDown(input, { key: "End" })).toBe(true);
  });

  it("still jump the grid from anywhere else in it", () => {
    renderGrid();
    const text = screen.getByTestId("text-diesel");
    expect(fireEvent.keyDown(text, { key: "Home" })).toBe(false);
    expect(fireEvent.keyDown(text, { key: "End" })).toBe(false);
  });
});
