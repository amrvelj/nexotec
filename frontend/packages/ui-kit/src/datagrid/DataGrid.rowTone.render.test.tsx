// @vitest-environment jsdom
import { describe, expect, it, vi } from "vitest";
import { render, screen, within } from "@testing-library/react";
import { MantineProvider } from "@mantine/core";
import { testTheme } from "../test/testTheme";
import { DataGrid } from "./DataGrid";
import type { GridColumnDef } from "./types";
import { FR_DATA_GRID_LABELS } from "../test/frenchLabels";
import { semantic } from "../tokens";

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

// KAN-516 — the administration lists moved onto the grid keep what their
// tables showed: a list named by its screen (a standard-text category), and
// a reference value missing a language tinted as a whole row.

interface Row {
  id: string;
  complete: boolean;
}

const columns: GridColumnDef<Row>[] = [{ id: "id", header: "CODE", cell: ({ row }) => row.original.id }];

function renderGrid(props: { ariaLabel?: string; rowTone?: (row: Row) => "destructive" | undefined }) {
  render(
    <MantineProvider theme={testTheme}>
      <DataGrid<Row>
        columns={columns}
        rows={[
          { id: "diesel", complete: true },
          { id: "petrol", complete: false },
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
        labels={FR_DATA_GRID_LABELS}
        emptyState={{ icon: null, title: "", description: "" }}
        {...props}
      />
    </MantineProvider>,
  );
}

const rowOf = (text: string) => screen.getByText(text).closest('[role="row"]') as HTMLElement;

describe("DataGrid — accessible name and row tone (KAN-516)", () => {
  it("names the table with the caller's label", () => {
    renderGrid({ ariaLabel: "Documents" });
    const table = screen.getByRole("table", { name: "Documents" });
    expect(within(table).getByText("petrol")).toBeInTheDocument();
  });

  it("tints only the rows the caller marks, with the semantic surface", () => {
    renderGrid({ rowTone: (row) => (row.complete ? undefined : "destructive") });
    expect(rowOf("petrol").style.backgroundColor).toBe(semantic.destructive.surface);
    expect(rowOf("diesel").style.backgroundColor).toBe("");
  });

  it("tints nothing without a rowTone", () => {
    renderGrid({});
    expect(rowOf("petrol").style.backgroundColor).toBe("");
  });
});
