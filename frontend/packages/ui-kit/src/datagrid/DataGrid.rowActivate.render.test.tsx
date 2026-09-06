// @vitest-environment jsdom
import { describe, expect, it, vi } from "vitest";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { DataGrid } from "./DataGrid";
import type { GridColumnDef } from "./types";

// jsdom has no layout, so TanStack Virtual measures every element as 0 and
// windows down to zero rows (the repo's other DataGrid render tests only
// assert on headers for this reason). Mock the virtualizer to a plain
// "render every row" so this test can exercise real row interaction.
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

// C-C (KAN-41) — `onRowActivate` is the generic row-pick callback the
// configurator's "Find the car" browse needs. Distinct from `rowHref`
// (navigation) and `selection` (bulk); a cell's own control still wins.

interface Row {
  id: string;
  name: string;
}

const columns: GridColumnDef<Row>[] = [
  { id: "name", header: "Name", cell: ({ row }) => row.original.name },
  {
    id: "act",
    header: "Act",
    cell: () => (
      <button type="button" onClick={(e) => e.stopPropagation()}>
        cell button
      </button>
    ),
  },
];

function grid(onRowActivate: (row: Row) => void) {
  return (
    <DataGrid<Row>
      columns={columns}
      rows={[
        { id: "a", name: "Alfa" },
        { id: "b", name: "Bravo" },
      ]}
      getRowId={(r) => r.id}
      sort={[]}
      onSortChange={() => {}}
      density="default"
      loading={false}
      fetchingNextPage={false}
      hasNextPage={false}
      onLoadMore={() => {}}
      total={2}
      totalIsEstimate={false}
      isFiltered={false}
      onRowActivate={onRowActivate}
      emptyState={{ icon: null, title: "", description: "" }}
    />
  );
}

describe("DataGrid onRowActivate", () => {
  it("fires on row click and on Enter, with the row's data", async () => {
    const user = userEvent.setup();
    const onRowActivate = vi.fn();
    render(grid(onRowActivate));

    await user.click(screen.getByText("Alfa"));
    expect(onRowActivate).toHaveBeenCalledWith({ id: "a", name: "Alfa" });

    const rows = screen.getAllByRole("row").filter((r) => r.classList.contains("dg-row"));
    rows[1].focus();
    await user.keyboard("{Enter}");
    expect(onRowActivate).toHaveBeenLastCalledWith({ id: "b", name: "Bravo" });
  });

  it("a cell's own control does not activate the row", async () => {
    const user = userEvent.setup();
    const onRowActivate = vi.fn();
    render(grid(onRowActivate));

    await user.click(screen.getAllByText("cell button")[0]);
    expect(onRowActivate).not.toHaveBeenCalled();
  });
});
