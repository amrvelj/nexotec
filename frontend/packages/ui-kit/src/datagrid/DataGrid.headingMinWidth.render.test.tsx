// @vitest-environment jsdom
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { render, screen } from "@testing-library/react";
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

// KAN-578 (Anto, 2026-11-07, option A) — the Stock grid's 300 px «Fahrzeug»
// column left the other columns at the kit's 120 px floor, and
// «KILOMETERSTAND» (139 px) was clipped. A column without a width of its own
// is now never narrower than its heading: each header reports what its label
// and sort indicator need, and the column's header and body cells take it as
// their minimum (the grid scrolls sideways instead). A column with a width —
// its own or the user's — keeps it.
//
// jsdom measures nothing, so the heading's `scrollWidth` is stubbed: each
// heading needs as many pixels as `HEADING_WIDTHS` says for its text.

const HEADING_WIDTHS: Record<string, number> = { CHILOMETRAGGIO: 111, VEICOLO: 60, ID: 20 };

interface Row {
  id: string;
  km: string;
  label: string;
}

const columns: GridColumnDef<Row>[] = [
  { id: "id", header: "ID", cell: ({ row }) => row.original.id },
  { id: "label", header: "VEICOLO", cell: ({ row }) => row.original.label, meta: { width: 300 } },
  { id: "km", header: "CHILOMETRAGGIO", cell: ({ row }) => row.original.km },
];

let scrollWidth: PropertyDescriptor | undefined;
beforeEach(() => {
  scrollWidth = Object.getOwnPropertyDescriptor(HTMLElement.prototype, "scrollWidth");
  Object.defineProperty(HTMLElement.prototype, "scrollWidth", {
    configurable: true,
    get(this: HTMLElement) {
      return this.dataset.headingLabel !== undefined ? (HEADING_WIDTHS[this.textContent ?? ""] ?? 0) : 0;
    },
  });
});
afterEach(() => {
  if (scrollWidth) Object.defineProperty(HTMLElement.prototype, "scrollWidth", scrollWidth);
  else delete (HTMLElement.prototype as { scrollWidth?: number }).scrollWidth;
});

function grid() {
  return (
    <DataGrid<Row>
      columns={columns}
      rows={[{ id: "S-1", km: "23'000", label: "Škoda Octavia Combi 2.0 TDI RS 4x4" }]}
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
  );
}

const header = (text: string) => screen.getByRole("columnheader", { name: text });
const cell = (text: string) => screen.getByText(text).closest('[role="cell"]') as HTMLElement;

describe("DataGrid — a column is never narrower than its heading (KAN-578)", () => {
  it("an unsized column takes its heading's width (plus padding) as the minimum, header and body alike", () => {
    render(grid());
    // 111 px of heading + 28 px of padding: above the 120 px floor.
    expect(header("CHILOMETRAGGIO").style.minWidth).toBe("139px");
    expect(cell("23'000").style.minWidth).toBe("139px");
  });

  it("a short heading keeps the 120 px floor", () => {
    render(grid());
    expect(header("ID").style.minWidth).toBe("120px");
    expect(cell("S-1").style.minWidth).toBe("120px");
  });

  it("a column with a width of its own keeps it", () => {
    render(grid());
    expect(header("VEICOLO").style.minWidth).toBe("300px");
    expect(cell("Škoda Octavia Combi 2.0 TDI RS 4x4").style.minWidth).toBe("300px");
  });
});
