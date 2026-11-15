// @vitest-environment jsdom
import { describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MantineProvider } from "@mantine/core";
import { testTheme } from "../test/testTheme";
import { DataGrid } from "./DataGrid";
import { FilterBuilder } from "./FilterBuilder";
import type { GridColumnDef } from "./types";
import {
  conditionsForField,
  describePredicate,
  hasCompleteValue,
  offeredPredicates,
  type FilterFieldDef,
  type FilterPredicate,
} from "./filterPredicate";
import { FR_CONDITION_LABELS, FR_DATA_GRID_LABELS, FR_FILTER_BUILDER_LABELS } from "../test/frenchLabels";

// KAN-461 — the kit's side of «every column sortable and filterable»:
// a grid on the list-query layer says which headers sort (from the served
// schema), and its fields may offer «zwischen» and «am / vor / nach» a day
// (Anto, 2026-11-15). A grid that does not list them never offers them.

vi.mock("@tanstack/react-virtual", () => ({
  useVirtualizer: ({ count, estimateSize }: { count: number; estimateSize: () => number }) => {
    const size = estimateSize();
    return {
      getVirtualItems: () => Array.from({ length: count }, (_, index) => ({ index, key: index, start: index * size, size })),
      getTotalSize: () => count * size,
      measure: () => {},
    };
  },
}));

const PRICE: FilterFieldDef = {
  id: "listPrice", label: "Prix", type: "number", numberFormat: "chf", conditions: ["atLeast", "between"],
};
const KM: FilterFieldDef = { id: "odometerKm", label: "Kilométrage", type: "number", conditions: ["between"] };
const BOUGHT: FilterFieldDef = { id: "purchaseDate", label: "Acheté", type: "date", conditions: ["today", "on", "before", "after"] };

describe("the extended conditions (KAN-461)", () => {
  it("are offered only by a field that lists them", () => {
    expect(conditionsForField({ id: "d", label: "D", type: "date" })).not.toContain("on");
    expect(conditionsForField({ id: "n", label: "N", type: "number" })).not.toContain("between");
    expect(conditionsForField(BOUGHT)).toEqual(["today", "on", "before", "after"]);
    expect(conditionsForField(PRICE)).toEqual(["atLeast", "between"]);
  });

  it("a predicate is sent only with every value its condition needs", () => {
    const base = { id: "p", fieldId: "x" } as const;
    expect(hasCompleteValue({ ...base, type: "number", condition: "between", value: 1, valueTo: 2 })).toBe(true);
    expect(hasCompleteValue({ ...base, type: "number", condition: "between", value: 3, valueTo: 2 })).toBe(false);
    expect(hasCompleteValue({ ...base, type: "number", condition: "between", value: 3 })).toBe(false);
    expect(hasCompleteValue({ ...base, type: "date", condition: "on", value: "2026-02-28" })).toBe(true);
    expect(hasCompleteValue({ ...base, type: "date", condition: "on", value: "2026-02-30" })).toBe(false);
    expect(hasCompleteValue({ ...base, type: "date", condition: "before", value: "" })).toBe(false);
    const stored: FilterPredicate[] = [{ id: "a", fieldId: "purchaseDate", type: "date", condition: "after" }];
    expect(offeredPredicates(stored, [BOUGHT])).toEqual([]);
  });

  it("reads a range and a day in the caller's words", () => {
    const range: FilterPredicate = { id: "r", fieldId: "listPrice", type: "number", condition: "between", value: 10000, valueTo: 25000 };
    expect(describePredicate(range, PRICE, FR_CONDITION_LABELS)).toBe("Prix : entre CHF 10'000.00 – CHF 25'000.00");
    const day: FilterPredicate = { id: "d", fieldId: "purchaseDate", type: "date", condition: "before", value: "2026-03-12" };
    expect(describePredicate(day, BOUGHT, FR_CONDITION_LABELS)).toBe("Acheté : avant le 12.03.2026");
    expect(describePredicate(day, BOUGHT, { ...FR_CONDITION_LABELS, formatDay: () => "12/03/2026" })).toBe(
      "Acheté : avant le 12/03/2026",
    );
  });
});

function renderBuilder(fields: FilterFieldDef[]) {
  const onChange = vi.fn();
  render(
    <MantineProvider theme={testTheme}>
      <FilterBuilder fields={fields} predicates={[]} onChange={onChange} labels={FR_FILTER_BUILDER_LABELS} />
    </MantineProvider>,
  );
  return onChange;
}

const saveButton = () => screen.getByRole("button", { name: "Enregistrer" });

describe("FilterBuilder — «zwischen» and «am / vor / nach»", () => {
  it("takes a range as one predicate, the lower bound first", async () => {
    const onChange = renderBuilder([KM]);
    await userEvent.click(screen.getByRole("button", { name: /Nouveau filtre/ }));
    fireEvent.change(screen.getByLabelText("De"), { target: { value: "50000" } });
    fireEvent.change(screen.getByLabelText("À"), { target: { value: "10000" } });

    expect(screen.getByRole("alert")).toHaveTextContent(FR_FILTER_BUILDER_LABELS.invalidRange);
    expect(saveButton()).toBeDisabled();

    fireEvent.change(screen.getByLabelText("À"), { target: { value: "90000" } });
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
    await userEvent.click(saveButton());
    const [saved] = onChange.mock.calls.at(-1)![0] as FilterPredicate[];
    expect(saved).toMatchObject({ fieldId: "odometerKm", condition: "between", value: 50000, valueTo: 90000 });
  });

  it("takes a calendar day for «avant le», and saves nothing without one", async () => {
    const onChange = renderBuilder([BOUGHT]);
    await userEvent.click(screen.getByRole("button", { name: /Nouveau filtre/ }));
    const [, conditionSelect] = screen.getAllByRole("combobox");
    fireEvent.change(conditionSelect, { target: { value: "before" } });
    expect(saveButton()).toBeDisabled();

    fireEvent.change(screen.getByLabelText("Date"), { target: { value: "2026-03-12" } });
    await userEvent.click(saveButton());
    const [saved] = onChange.mock.calls.at(-1)![0] as FilterPredicate[];
    expect(saved).toMatchObject({ fieldId: "purchaseDate", condition: "before", value: "2026-03-12" });
  });
});

interface Row {
  id: string;
}

const columns: GridColumnDef<Row>[] = [
  { id: "stockNumber", header: "N°", cell: ({ row }) => row.original.id, meta: { sortField: "stockNumber" } },
  { id: "listPrice", header: "Prix", cell: () => "—" },
  { id: "notes", header: "Notes", cell: () => "—", meta: { sortField: "notes" } },
];

function renderGrid(sortableFields?: ReadonlySet<string>) {
  render(
    <MantineProvider theme={testTheme}>
      <DataGrid<Row>
        columns={columns}
        rows={[{ id: "L-1" }]}
        getRowId={(r) => r.id}
        sort={[]}
        onSortChange={() => {}}
        sortableFields={sortableFields}
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

const sortableHeaders = () =>
  screen.getAllByRole("columnheader").filter((h) => h.hasAttribute("aria-sort")).map((h) => h.textContent);

describe("DataGrid — sortableFields (KAN-461)", () => {
  it("sorts exactly the served fields, whatever meta.sortField says", () => {
    renderGrid(new Set(["stockNumber", "listPrice"]));
    expect(sortableHeaders()).toEqual(["N°", "Prix"]);
  });

  it("leaves a grid that passes none to meta.sortField", () => {
    renderGrid();
    expect(sortableHeaders()).toEqual(["N°", "Notes"]);
  });
});
