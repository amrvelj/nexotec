import { describe, expect, it } from "vitest";
import { sortRowsInBrowser, sortSpecFromQueryParam, sortSpecToQueryParam } from "./sorting";
import type { SortSpec } from "./types";

// KAN-516 — the short administration lists sort in the browser.

interface Row {
  id: string;
  title: string | null;
  count: number;
  isDefault: boolean;
}

const rows: Row[] = [
  { id: "a", title: "Zahlung", count: 2, isDefault: false },
  { id: "b", title: "ägypten", count: 10, isDefault: true },
  { id: "c", title: null, count: 1, isDefault: false },
  { id: "d", title: "Code 10", count: 2, isDefault: true },
  { id: "e", title: "Code 9", count: 3, isDefault: false },
];

const columns = [
  { meta: { sortField: "title", sortValue: (r: Row) => r.title } },
  { meta: { sortField: "count", sortValue: (r: Row) => r.count } },
  { meta: { sortField: "isDefault", sortValue: (r: Row) => r.isDefault } },
  { meta: { sortField: "noValue" } },
];

const ids = (sorted: Row[]) => sorted.map((r) => r.id);
const sortBy = (sort: SortSpec[]) => ids(sortRowsInBrowser(rows, sort, columns, "de-CH"));

describe("sortRowsInBrowser", () => {
  it("keeps the order the rows came in for an empty sort, and never mutates them", () => {
    const before = [...rows];
    expect(sortBy([])).toEqual(["a", "b", "c", "d", "e"]);
    sortBy([{ field: "title", direction: "desc" }]);
    expect(rows).toEqual(before);
  });

  it("compares text in the reader's language, digits in numeric order, the empty value last", () => {
    expect(sortBy([{ field: "title", direction: "asc" }])).toEqual(["b", "e", "d", "a", "c"]);
    expect(sortBy([{ field: "title", direction: "desc" }])).toEqual(["a", "d", "e", "b", "c"]);
  });

  it("compares numbers and booleans by value, level by level, equal rows in their own order", () => {
    expect(sortBy([{ field: "count", direction: "desc" }])).toEqual(["b", "e", "a", "d", "c"]);
    expect(
      sortBy([
        { field: "isDefault", direction: "desc" },
        { field: "count", direction: "asc" },
      ])
    ).toEqual(["d", "b", "c", "a", "e"]);
  });

  it("ignores a field no column sorts by", () => {
    expect(sortBy([{ field: "unknown", direction: "asc" }])).toEqual(["a", "b", "c", "d", "e"]);
    expect(sortBy([{ field: "noValue", direction: "asc" }])).toEqual(["a", "b", "c", "d", "e"]);
  });
});

describe("sortSpecFromQueryParam", () => {
  it("reads back what sortSpecToQueryParam wrote, at most three levels", () => {
    const sort: SortSpec[] = [
      { field: "title", direction: "asc" },
      { field: "count", direction: "desc" },
    ];
    expect(sortSpecFromQueryParam(sortSpecToQueryParam(sort))).toEqual(sort);
    expect(sortSpecFromQueryParam("a:desc,b,c:sideways,d:asc")).toEqual([
      { field: "a", direction: "desc" },
      { field: "b", direction: "asc" },
      { field: "c", direction: "asc" },
    ]);
    expect(sortSpecFromQueryParam(null)).toEqual([]);
    expect(sortSpecFromQueryParam(",:desc")).toEqual([]);
  });
});
