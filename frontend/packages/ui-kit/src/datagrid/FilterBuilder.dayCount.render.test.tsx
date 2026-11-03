// @vitest-environment jsdom
import { describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MantineProvider } from "@mantine/core";
import { FilterBuilder } from "./FilterBuilder";
import { ViewsAndFilters } from "./ViewsAndFilters";
import { offeredPredicates, type FilterFieldDef, type FilterPredicate } from "./filterPredicate";
import type { SavedView } from "./savedView";
import { FR_FILTER_BUILDER_LABELS, FR_VIEWS_AND_FILTERS_LABELS } from "../test/frenchLabels";
import { testTheme } from "../test/testTheme";

// KAN-475 — the builder accepted a fraction, a negative count or more than
// 36500, which the list endpoints refuse with a 422 (they take 0 to 36500):
// the grid then showed its error state instead of a refusal where the number
// was typed. And a saved
// view whose filters this grid partly drops (ADR-102: quietly) still showed
// its name as applied.

const CHANGED: FilterFieldDef = { id: "updatedAt", label: "Modifié", type: "date", conditions: ["inTheLastDays", "moreThanDaysAgo"] };
const STATUS: FilterFieldDef = { id: "status", label: "Statut", type: "select", options: [{ value: "active", label: "Actif" }], conditions: ["is"] };
const INVALID = FR_FILTER_BUILDER_LABELS.invalidDayCount(0, 36500);

function renderBuilder(predicates: FilterPredicate[] = [], fields: FilterFieldDef[] = [CHANGED]) {
  const onChange = vi.fn();
  render(
    <MantineProvider theme={testTheme}>
      <FilterBuilder fields={fields} predicates={predicates} onChange={onChange} labels={FR_FILTER_BUILDER_LABELS} />
    </MantineProvider>,
  );
  return onChange;
}

const dayInput = () => screen.getByRole("spinbutton", { name: FR_FILTER_BUILDER_LABELS.dayCount });
const saveButton = () => screen.getByRole("button", { name: "Enregistrer" });

describe("FilterBuilder — the day count is refused in place (KAN-475)", () => {
  for (const typed of ["-1", "1.5", "36501", ""]) {
    it(`refuses «${typed}» beside the input, in the caller's words, and saves nothing`, async () => {
      const onChange = renderBuilder();
      await userEvent.click(screen.getByRole("button", { name: /Nouveau filtre/ }));
      fireEvent.change(dayInput(), { target: { value: typed } });

      expect(screen.getByRole("alert")).toHaveTextContent(INVALID);
      expect(dayInput()).toHaveAttribute("aria-invalid", "true");
      expect(saveButton()).toBeDisabled();
      await userEvent.click(saveButton());
      expect(onChange).not.toHaveBeenCalled();
    });
  }

  for (const days of [0, 45, 36500]) {
    it(`saves ${days} days, a whole number the endpoints take, with no refusal shown`, async () => {
      const onChange = renderBuilder();
      await userEvent.click(screen.getByRole("button", { name: /Nouveau filtre/ }));
      fireEvent.change(dayInput(), { target: { value: String(days) } });

      expect(screen.queryByRole("alert")).not.toBeInTheDocument();
      await userEvent.click(saveButton());
      const [saved] = onChange.mock.calls.at(-1)![0] as FilterPredicate[];
      expect([saved.condition, saved.days]).toEqual(["inTheLastDays", days]);
    });
  }

  it("a date filter switched to an N-days condition starts at 30 days, never without a count", async () => {
    const today: FilterFieldDef = { id: "createdAt", label: "Créé", type: "date" };
    const onChange = renderBuilder([], [today]);
    await userEvent.click(screen.getByRole("button", { name: /Nouveau filtre/ }));
    const [, conditionSelect] = screen.getAllByRole("combobox");
    fireEvent.change(conditionSelect, { target: { value: "moreThanDaysAgo" } });

    expect(dayInput()).toHaveValue(30);
    await userEvent.click(saveButton());
    const [saved] = onChange.mock.calls.at(-1)![0] as FilterPredicate[];
    expect([saved.condition, saved.days]).toEqual(["moreThanDaysAgo", 30]);
  });
});

describe("ViewsAndFilters — a view this grid narrows is not shown as applied (KAN-475)", () => {
  const view = (filters: FilterPredicate[]): SavedView => ({ id: "v1", name: "Mes clients", snapshot: { filters } });
  const ACTIVE: FilterPredicate = { id: "a", fieldId: "status", type: "select", condition: "is", value: "active" };
  const IS_NOT: FilterPredicate = { id: "b", fieldId: "status", type: "select", condition: "isNot", value: "active" };
  const FRACTION_OF_DAYS: FilterPredicate = { id: "c", fieldId: "updatedAt", type: "date", condition: "inTheLastDays", days: 1.5 };

  /** Opens the panel and returns the saved view's row in its Views list. */
  async function listedViewRow(trigger: HTMLElement): Promise<HTMLElement> {
    await userEvent.click(trigger);
    await screen.findByText(FR_VIEWS_AND_FILTERS_LABELS.viewsHeading);
    const name = screen.getAllByText("Mes clients").find((el) => !trigger.contains(el))!;
    return name.parentElement!;
  }

  function renderViews(saved: SavedView, predicates: FilterPredicate[]) {
    render(
      <MantineProvider theme={testTheme}>
        <ViewsAndFilters
          currentViewName={saved.name}
          views={[saved]}
          onApplyView={() => {}}
          onSaveCurrentAsView={() => {}}
          onDeleteView={() => {}}
          onSetDefaultView={() => {}}
          fields={[STATUS, CHANGED]}
          predicates={predicates}
          onPredicatesChange={() => {}}
          onResetFilters={() => {}}
          narrowPredicates={(stored) => offeredPredicates(stored, [STATUS, CHANGED])}
          labels={FR_VIEWS_AND_FILTERS_LABELS}
        />
      </MantineProvider>,
    );
  }

  it("marks the view with the unsaved-changes dot, and does not tick it, when a stored filter is dropped", async () => {
    // A withdrawn «is not» and a day count the endpoint refuses: both dropped quietly.
    renderViews(view([ACTIVE, IS_NOT, FRACTION_OF_DAYS]), [ACTIVE]);

    const trigger = screen.getByRole("button", { name: /Mes clients/ });
    expect(screen.getByRole("img", { name: FR_VIEWS_AND_FILTERS_LABELS.viewChanged })).toBeInTheDocument();
    expect(await listedViewRow(trigger)).not.toContainElement(document.querySelector("svg.lucide-check"));
    expect(document.querySelector("svg.lucide-check")).toBeNull();
  });

  it("shows a view whose filters are all applied as applied — named, ticked, no dot", async () => {
    renderViews(view([ACTIVE]), [ACTIVE]);

    expect(screen.queryByRole("img", { name: FR_VIEWS_AND_FILTERS_LABELS.viewChanged })).not.toBeInTheDocument();
    const row = await listedViewRow(screen.getByRole("button", { name: /Mes clients/ }));
    expect(row.querySelector("svg.lucide-check")).not.toBeNull();
  });
});
