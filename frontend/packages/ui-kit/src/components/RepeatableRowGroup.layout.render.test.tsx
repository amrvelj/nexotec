// @vitest-environment jsdom
import { afterEach, describe, expect, it, vi } from "vitest";
import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MantineProvider } from "@mantine/core";
import { RepeatableRowGroup, type RepeatableRowGroupProps } from "./RepeatableRowGroup";
import { FR_REPEATABLE_ROW_LABELS as LABELS } from "../test/frenchLabels";

// KAN-460 — the contact rows' «Label» box had only a placeholder, nobody
// knew what it was for, and phone and e-mail rows did not line up. Every
// row now sits on one column template (star · type · value · label ·
// consent · menu), named once per group in a header row, the label and
// consent columns with a hint.
//
// KAN-267 — in a narrow card the row's other controls took the width and
// the value editor collapsed to a few pixels. A group too narrow for the
// template at its value minimum stacks each row's cells instead, each cell
// with its own label, the value editor never below its minimum.
//
// jsdom has no layout, so this pins the styles that make the browser do it
// (and fakes the measured width for the stacked case); the screenshot on the
// ticket shows the result.

const row = { id: "r1", type: "mobile", value: "+41791234567", isPrimary: true, consentGranted: false };

function renderGroup(overrides: Partial<RepeatableRowGroupProps> = {}) {
  return render(
    <MantineProvider>
      <RepeatableRowGroup
        mode="detail"
        label="Phones"
        valueLabel="Numéro"
        addLabel="Add phone"
        formerLabel="Former"
        typeOptions={[{ value: "mobile", label: "Mobile" }]}
        defaultType="mobile"
        rows={[row]}
        renderValueEditor={(value, onChange, _autoFocus, name) => <input aria-label={name} value={value} onChange={(e) => onChange(e.target.value)} />}
        onCreate={async () => {}}
        onUpdate={async () => {}}
        onDelete={async () => {}}
        labels={LABELS}
        {...overrides}
      />
    </MantineProvider>,
  );
}

const part = (el: HTMLElement, name: string) => el.closest(`[data-row-part="${name}"]`) as HTMLElement;
const rowOf = (el: HTMLElement) => el.closest("[data-row-layout]") as HTMLElement;

/** Every element reports `width` — as a card of that width would. */
function fakeGroupWidth(width: number) {
  vi.spyOn(HTMLElement.prototype, "getBoundingClientRect").mockReturnValue({
    width, height: 40, top: 0, left: 0, right: width, bottom: 40, x: 0, y: 0, toJSON: () => ({}),
  } as DOMRect);
}

afterEach(() => vi.restoreAllMocks());

describe("RepeatableRowGroup column template (KAN-460)", () => {
  it("names the columns once, in a header on the rows' own template", () => {
    renderGroup({ valueEditorMinWidth: 432 });
    const header = document.querySelector('[data-row-part="header"]') as HTMLElement;
    const valueInput = screen.getByRole("textbox", { name: "Numéro" });

    expect(header).toHaveTextContent("Type");
    expect(header).toHaveTextContent("Numéro");
    expect(header).toHaveTextContent(LABELS.labelColumn);
    expect(header).toHaveTextContent(LABELS.consent);
    expect(header.dataset.columnTemplate).toBe(rowOf(valueInput).dataset.columnTemplate);
    expect(rowOf(valueInput).dataset.columnTemplate).toBe("18px 130px minmax(min(432px, 100%), 1fr) 140px 104px 28px");
  });

  it("gives the label input and the consent box an accessible name and their hint, never a placeholder alone", () => {
    renderGroup();
    const labelInput = screen.getByRole("textbox", { name: LABELS.labelColumn });
    const consent = screen.getByRole("checkbox", { name: LABELS.consent });

    expect(labelInput).not.toHaveAttribute("placeholder");
    expect(labelInput).toHaveAccessibleDescription(LABELS.labelHint);
    expect(consent).toHaveAccessibleDescription(LABELS.consentHint);
    expect(screen.getByRole("combobox", { name: LABELS.typeColumn })).toBeInTheDocument();
    // The header's two hints are info icons beside the column names (KAN-443).
    const header = document.querySelector('[data-row-part="header"]') as HTMLElement;
    expect(within(header).getAllByRole("img", { name: LABELS.hintIcon })).toHaveLength(2);
  });

  it("puts two groups with the same value minimum on the same template", () => {
    renderGroup({ valueEditorMinWidth: 432 });
    renderGroup({ label: "E-mails", valueLabel: "Adresse", valueEditorMinWidth: 432 });

    const phoneRow = rowOf(screen.getByRole("textbox", { name: "Numéro" }));
    const emailRow = rowOf(screen.getByRole("textbox", { name: "Adresse" }));
    expect(emailRow.dataset.columnTemplate).toBe(phoneRow.dataset.columnTemplate);
  });

  it("keeps the add row on the template, Save and Cancel under the label, consent and menu columns", async () => {
    const user = userEvent.setup();
    renderGroup({ rows: [], valueEditorMinWidth: 360 });
    expect(document.querySelector('[data-row-part="header"]')).toBeNull(); // nothing to name yet
    await user.click(screen.getByRole("button", { name: /Add phone/ }));

    const value = screen.getByRole("textbox", { name: "Numéro" });
    const trailing = part(screen.getByRole("button", { name: LABELS.save }), "trailing");
    expect(rowOf(value).dataset.columnTemplate).toContain("minmax(min(360px, 100%), 1fr)");
    expect(trailing.style.gridColumn).toBe("4 / span 3");
    expect(trailing).toContainElement(screen.getByRole("button", { name: LABELS.cancel }));
    expect(document.querySelector('[data-row-part="header"]')).not.toBeNull();
  });
});

describe("RepeatableRowGroup in a narrow card (KAN-267, KAN-460)", () => {
  it("stacks the cells, each with its own label, and keeps the value editor at its minimum", () => {
    fakeGroupWidth(500); // narrower than the template at 432px
    renderGroup({ valueEditorMinWidth: 432 });

    const valueInput = screen.getByRole("textbox", { name: "Numéro" });
    const stackedRow = rowOf(valueInput);
    expect(stackedRow.dataset.rowLayout).toBe("stacked");
    expect(stackedRow.style.flexWrap).toBe("wrap");
    expect(part(valueInput, "value").style.flex).toBe("1 1 432px");
    expect(part(valueInput, "value").style.minWidth).toBe("min(432px, 100%)");
    // No header: every cell says what it is.
    expect(document.querySelector('[data-row-part="header"]')).toBeNull();
    expect(part(valueInput, "value")).toHaveTextContent("Numéro");
    const labelCell = part(screen.getByRole("textbox", { name: LABELS.labelColumn }), "label");
    expect(labelCell).toHaveTextContent(LABELS.labelColumn);
    expect(within(labelCell).getByRole("img", { name: LABELS.hintIcon })).toBeInTheDocument();
    expect(part(screen.getByRole("checkbox", { name: LABELS.consent }), "consent")).toHaveTextContent(LABELS.consent);
  });

  it("keeps the add row's labels when it stacks", async () => {
    const user = userEvent.setup();
    fakeGroupWidth(560);
    renderGroup({ valueEditorMinWidth: 270 });
    await user.click(screen.getByRole("button", { name: /Add phone/ }));

    const addRow = rowOf(screen.getByRole("button", { name: LABELS.save }));
    expect(addRow.dataset.rowLayout).toBe("stacked");
    const value = within(addRow).getByRole("textbox", { name: "Numéro" });
    expect(part(value, "value")).toHaveTextContent("Numéro");
    expect(part(within(addRow).getByRole("combobox", { name: LABELS.typeColumn }), "type")).toHaveTextContent(LABELS.typeColumn);
  });

  it("uses the template once the card is wide enough", () => {
    fakeGroupWidth(1200);
    renderGroup({ valueEditorMinWidth: 432 });
    expect(rowOf(screen.getByRole("textbox", { name: "Numéro" })).dataset.rowLayout).toBe("template");
  });
});
