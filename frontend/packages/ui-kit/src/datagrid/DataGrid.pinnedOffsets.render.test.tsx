// @vitest-environment jsdom
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
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

// KAN-582 — the selection column and a left-pinned data column both stuck at
// `left: 0`, so scrolled sideways the identifier slid over the checkboxes.
// A left-pinned column now sticks after the left-pinned columns before it:
// the select column's 40 px, a sized column's own width, an unsized column's
// rendered width (jsdom lays nothing out, so `getBoundingClientRect` is
// stubbed: an unsized pinned header renders `RENDERED_WIDTH` px wide).

const RENDERED_WIDTH = 173;

interface Row {
  id: string;
  name: string;
  city: string;
}

const columns: GridColumnDef<Row>[] = [
  { id: "id", header: "NUMÉRO", cell: ({ row }) => row.original.id, meta: { pinned: "left" } },
  { id: "name", header: "NOM", cell: ({ row }) => row.original.name, meta: { pinned: "left", width: 210 } },
  { id: "city", header: "LOCALITÉ", cell: ({ row }) => row.original.city },
];

let rect: PropertyDescriptor | undefined;
beforeEach(() => {
  rect = Object.getOwnPropertyDescriptor(HTMLElement.prototype, "getBoundingClientRect");
  Object.defineProperty(HTMLElement.prototype, "getBoundingClientRect", {
    configurable: true,
    value(this: HTMLElement) {
      const width = this.getAttribute("role") === "columnheader" ? RENDERED_WIDTH : 0;
      return { width, height: 0, top: 0, left: 0, right: width, bottom: 0, x: 0, y: 0, toJSON: () => ({}) };
    },
  });
});
afterEach(() => {
  if (rect) Object.defineProperty(HTMLElement.prototype, "getBoundingClientRect", rect);
});

const selection = {
  selectedIds: new Set<string>(),
  onSelectionChange: () => {},
  selectAllLabel: "Tout sélectionner",
  selectRowLabel: (row: Row) => `Sélectionner ${row.id}`,
};

function grid(extra: Partial<Parameters<typeof DataGrid<Row>>[0]> = {}) {
  return (
    <MantineProvider theme={testTheme}>
      <DataGrid<Row>
        columns={columns}
        rows={[{ id: "K-1", name: "Müller Hans", city: "Bern" }]}
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
        {...extra}
      />
    </MantineProvider>
  );
}

const header = (text: string) => screen.getByRole("columnheader", { name: text });
const cell = (text: string) => screen.getByText(text).closest('[role="cell"]') as HTMLElement;

describe("DataGrid — left-pinned columns stick one after the other (KAN-582)", () => {
  it("with a selection, the first pinned column sticks after the 40 px checkbox column", () => {
    render(grid({ selection }));
    const selectHeader = screen.getByRole("checkbox", { name: "Tout sélectionner" }).closest('[role="columnheader"]') as HTMLElement;
    const selectCell = screen.getByRole("checkbox", { name: "Sélectionner K-1" }).closest('[role="cell"]') as HTMLElement;
    expect(selectHeader.style.left).toBe("0px");
    expect(selectCell.style.left).toBe("0px");
    expect(header("NUMÉRO").style.left).toBe("40px");
    expect(cell("K-1").style.left).toBe("40px");
  });

  it("a second pinned column sticks after the first one's rendered width (cumulative offsets)", () => {
    render(grid({ selection }));
    // 40 (checkboxes) + 173 (NUMÉRO, unsized: its rendered width)
    expect(header("NOM").style.left).toBe(`${40 + RENDERED_WIDTH}px`);
    expect(cell("Müller Hans").style.left).toBe(`${40 + RENDERED_WIDTH}px`);
  });

  it("without a selection, the first pinned column sticks at 0", () => {
    render(grid());
    expect(header("NUMÉRO").style.left).toBe("0px");
    expect(cell("K-1").style.left).toBe("0px");
    expect(header("NOM").style.left).toBe(`${RENDERED_WIDTH}px`);
  });

  it("a sized pinned column adds its own width, and an unpinned column stays unstuck", () => {
    const sizedFirst: GridColumnDef<Row>[] = [
      { ...columns[1] },
      { ...columns[0] },
      columns[2],
    ];
    render(grid({ selection, columns: sizedFirst }));
    expect(header("NOM").style.left).toBe("40px");
    // 40 + 210 (NOM's own width, never measured)
    expect(header("NUMÉRO").style.left).toBe("250px");
    expect(header("LOCALITÉ").style.position).toBe("relative");
    expect(cell("Bern").style.left).toBe("");
  });
});
