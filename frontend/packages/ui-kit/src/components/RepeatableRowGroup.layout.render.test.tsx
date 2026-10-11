// @vitest-environment jsdom
import { describe, expect, it } from "vitest";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MantineProvider } from "@mantine/core";
import { RepeatableRowGroup, type RepeatableRowGroupProps } from "./RepeatableRowGroup";

// KAN-267 — in a narrow card the row's other controls took the width and the
// value editor (`flex: 1; min-width: 0`) collapsed to a few pixels: a phone's
// country and number read "Lan / d", "Nu / mm / er". The row now wraps, the
// value editor never shrinks below its minimum, and the label, consent and
// menu move together. jsdom has no layout, so this pins the styles that make
// the browser do it; the screenshot on the ticket shows the result.

const row = { id: "r1", type: "mobile", value: "+41791234567", isPrimary: true, consentGranted: false };

function renderGroup(overrides: Partial<RepeatableRowGroupProps> = {}) {
  return render(
    <MantineProvider>
      <RepeatableRowGroup
        mode="detail"
        label="Phones"
        addLabel="Add phone"
        formerLabel="Former"
        typeOptions={[{ value: "mobile", label: "Mobile" }]}
        defaultType="mobile"
        rows={[row]}
        renderValueEditor={(value, onChange) => <input aria-label="Value" value={value} onChange={(e) => onChange(e.target.value)} />}
        onCreate={async () => {}}
        onUpdate={async () => {}}
        onDelete={async () => {}}
        {...overrides}
      />
    </MantineProvider>,
  );
}

const part = (el: HTMLElement, name: string) => el.closest(`[data-row-part="${name}"]`) as HTMLElement;

describe("RepeatableRowGroup row layout (KAN-267)", () => {
  it("wraps the row and keeps the value editor at its minimum width instead of squeezing it", () => {
    renderGroup({ valueEditorMinWidth: 360 });
    const value = part(screen.getByRole("textbox", { name: "Value" }), "value");

    expect((value.parentElement as HTMLElement).style.flexWrap).toBe("wrap");
    expect(value.style.flex).toBe("1 1 360px");
    expect(value.style.minWidth).toBe("min(360px, 100%)");
  });

  it("defaults the minimum to 200px", () => {
    renderGroup();
    const value = part(screen.getByRole("textbox", { name: "Value" }), "value");

    expect(value.style.flex).toBe("1 1 200px");
  });

  it("keeps the label, consent and menu in one cluster that wraps as a unit, the label at its 90px", () => {
    renderGroup();
    const label = screen.getByPlaceholderText("Label");
    const trailing = part(label, "trailing");

    expect(trailing.style.marginLeft).toBe("auto");
    expect(trailing).toContainElement(screen.getByRole("checkbox"));
    expect(trailing).toContainElement(screen.getByRole("button", { name: "Phones row actions" }));
    // A native input's min-content width (~20 characters) used to override
    // its 90px basis, taking the value editor's room.
    expect(label.style.width).toBe("90px");
    expect(label.style.minWidth).toBe("0px");
  });

  it("gives the add row's value editor the same minimum, with Save and Cancel wrapping together", async () => {
    const user = userEvent.setup();
    renderGroup({ rows: [], valueEditorMinWidth: 360 });
    await user.click(screen.getByRole("button", { name: /Add phone/ }));

    const value = part(screen.getByRole("textbox", { name: "Value" }), "value");
    const trailing = part(screen.getByRole("button", { name: "Save" }), "trailing");

    expect(value.style.flex).toBe("1 1 360px");
    expect(trailing).toContainElement(screen.getByRole("button", { name: "Cancel" }));
  });
});
