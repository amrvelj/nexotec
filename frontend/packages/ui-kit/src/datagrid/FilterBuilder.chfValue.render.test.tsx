// @vitest-environment jsdom
import { describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MantineProvider } from "@mantine/core";
import { FilterBuilder } from "./FilterBuilder";
import type { FilterFieldDef, FilterPredicate } from "./filterPredicate";
import { FR_FILTER_BUILDER_LABELS } from "../test/frenchLabels";

// KAN-454 (ADR-076, KAN-168): a money filter's value was typed into a bare
// <input type="number"> — no CHF, no separator — while the saved filter's
// text read «CHF 40'000.00» (KAN-421). A field declared `numberFormat:
// "chf"` now takes its value through the kit's ChfInput; the predicate still
// stores a plain number, so the query parameter it becomes is unchanged.

const PRICE: FilterFieldDef = { id: "basePrice", label: "Prix catalogue", type: "number", numberFormat: "chf", conditions: ["atLeast", "atMost"] };
const PS: FilterFieldDef = { id: "ps", label: "PS", type: "number", conditions: ["atLeast"] };

function renderBuilder(fields: FilterFieldDef[]) {
  const onChange = vi.fn();
  render(
    <MantineProvider>
      <FilterBuilder fields={fields} predicates={[]} onChange={onChange} labels={FR_FILTER_BUILDER_LABELS} />
    </MantineProvider>,
  );
  return onChange;
}

describe("FilterBuilder — a CHF filter value (KAN-454)", () => {
  it("typing 40000 shows CHF 40'000.00 and stores 40000", async () => {
    const user = userEvent.setup();
    const onChange = renderBuilder([PRICE]);
    await user.click(screen.getByRole("button", { name: /Nouveau filtre/ }));

    expect(screen.queryByRole("spinbutton")).not.toBeInTheDocument();
    const amount = screen.getByRole("textbox", { name: "Prix catalogue" });
    await user.type(amount, "40000");
    expect(amount).toHaveValue("CHF 40'000");
    await user.tab();
    expect(amount).toHaveValue("CHF 40'000.00");

    await user.click(screen.getByRole("button", { name: "Enregistrer" }));
    const [saved] = onChange.mock.calls.at(-1)![0] as FilterPredicate[];
    expect(saved).toMatchObject({ fieldId: "basePrice", type: "number", value: 40000 });
    expect(typeof saved.value).toBe("number");
  });

  it("keeps a partial amount while it is typed: CHF 0.50 can be entered", async () => {
    const user = userEvent.setup();
    const onChange = renderBuilder([PRICE]);
    await user.click(screen.getByRole("button", { name: /Nouveau filtre/ }));

    const amount = screen.getByRole("textbox", { name: "Prix catalogue" });
    await user.type(amount, "0.");
    expect(amount).toHaveValue("CHF 0.");
    await user.type(amount, "5");
    expect(amount).toHaveValue("CHF 0.5");

    await user.click(screen.getByRole("button", { name: "Enregistrer" }));
    const [saved] = onChange.mock.calls.at(-1)![0] as FilterPredicate[];
    expect(saved.value).toBe(0.5);
  });

  it("a number field without the CHF format keeps its plain number input", async () => {
    const user = userEvent.setup();
    renderBuilder([PS]);
    await user.click(screen.getByRole("button", { name: /Nouveau filtre/ }));

    fireEvent.change(screen.getByRole("spinbutton"), { target: { value: "150" } });
    expect(screen.getByRole("spinbutton")).toHaveValue(150);
  });
});
