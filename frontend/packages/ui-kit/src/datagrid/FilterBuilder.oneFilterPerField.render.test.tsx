// @vitest-environment jsdom
import { describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MantineProvider } from "@mantine/core";
import { FilterBuilder } from "./FilterBuilder";
import type { FilterFieldDef, FilterPredicate } from "./filterPredicate";
import { FR_FILTER_BUILDER_LABELS } from "../test/frenchLabels";
import { testTheme } from "../test/testTheme";

// ADR-102 (Anto, 2026-10-26; KAN-458): a field holds one filter. Adding a
// filter on a field that already has one replaces it — the grid would
// otherwise drop the new one silently (offeredPredicates keeps the first).
// And no "Duplicate filter" action: a copy on the same field could only
// replace its original.

const PS: FilterFieldDef = { id: "ps", label: "PS", type: "number", conditions: ["equals", "atLeast", "atMost"] };
const DOORS: FilterFieldDef = { id: "doors", label: "Portes", type: "number", conditions: ["equals"] };
const AT_LEAST_150: FilterPredicate = { id: "p1", fieldId: "ps", type: "number", condition: "atLeast", value: 150 };
const DOORS_5: FilterPredicate = { id: "p2", fieldId: "doors", type: "number", condition: "equals", value: 5 };

function renderBuilder(predicates: FilterPredicate[]) {
  const onChange = vi.fn();
  render(
    <MantineProvider theme={testTheme}>
      <FilterBuilder fields={[PS, DOORS]} predicates={predicates} onChange={onChange} labels={FR_FILTER_BUILDER_LABELS} />
    </MantineProvider>,
  );
  return onChange;
}

describe("FilterBuilder — one filter per field (ADR-102)", () => {
  it("adding a filter on a field that has one replaces it, in its place", async () => {
    const onChange = renderBuilder([AT_LEAST_150, DOORS_5]);

    await userEvent.click(screen.getByRole("button", { name: /Nouveau filtre/ }));
    const [, conditionSelect] = screen.getAllByRole("combobox");
    fireEvent.change(conditionSelect, { target: { value: "atMost" } });
    fireEvent.change(screen.getByRole("spinbutton"), { target: { value: "250" } });
    await userEvent.click(screen.getByRole("button", { name: "Enregistrer" }));

    const next = onChange.mock.calls.at(-1)![0] as FilterPredicate[];
    expect(next.map((p) => [p.fieldId, p.condition, p.value])).toEqual([
      ["ps", "atMost", 250],
      ["doors", "equals", 5],
    ]);
  });

  it("offers only edit and delete on a saved filter — no «duplicate»", () => {
    renderBuilder([AT_LEAST_150]);
    const row = screen.getByRole("button", { name: "Modifier le filtre" }).parentElement!;
    expect(within(row).getAllByRole("button").map((b) => b.getAttribute("aria-label"))).toEqual([
      "Modifier le filtre",
      "Supprimer le filtre",
    ]);
  });

  it("the live preview counts what saving leaves — the draft in place of its field's filter", async () => {
    const onPreviewCount = vi.fn().mockResolvedValue(3);
    render(
      <MantineProvider theme={testTheme}>
        <FilterBuilder
          fields={[PS, DOORS]}
          predicates={[AT_LEAST_150, DOORS_5]}
          onChange={() => {}}
          onPreviewCount={onPreviewCount}
          labels={FR_FILTER_BUILDER_LABELS}
        />
      </MantineProvider>,
    );
    await userEvent.click(screen.getByRole("button", { name: /Nouveau filtre/ }));
    await waitFor(() => expect(onPreviewCount).toHaveBeenCalled());
    const counted = onPreviewCount.mock.calls.at(-1)![0] as FilterPredicate[];
    expect(counted.filter((p) => p.fieldId === "ps")).toHaveLength(1);
    expect(counted.map((p) => p.fieldId).sort()).toEqual(["doors", "ps"]);
  });
});
