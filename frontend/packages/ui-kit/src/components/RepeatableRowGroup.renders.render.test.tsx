// @vitest-environment jsdom
import { describe, expect, it, vi } from "vitest";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MantineProvider } from "@mantine/core";
import { RepeatableRowGroup } from "./RepeatableRowGroup";

// KAN-167 — the add-row draft was the group's own state, so every keystroke
// in the add row re-rendered every existing row's value editor (on the
// customer screens a phone's Mantine Select each). The draft now lives in the
// add row: typing renders that row only.

const rows = [
  { id: "r1", type: "mobile", value: "+41791110000", isPrimary: true, consentGranted: false },
  { id: "r2", type: "mobile", value: "+41792220000", isPrimary: false, consentGranted: false },
  { id: "r3", type: "work", value: "+41443330000", isPrimary: true, consentGranted: false },
];

function renderGroup(onCreate: (draft: { type: string; value: string }) => Promise<void> = async () => {}) {
  const renderValueEditor = vi.fn((value: string, onChange: (v: string) => void, autoFocus: boolean) => (
    <input aria-label={autoFocus ? "New value" : `Value ${value}`} value={value} onChange={(e) => onChange(e.target.value)} />
  ));
  render(
    <MantineProvider>
      <RepeatableRowGroup
        mode="detail"
        label="Phones"
        addLabel="Add phone"
        formerLabel="Former"
        typeOptions={[
          { value: "mobile", label: "Mobile" },
          { value: "work", label: "Work" },
        ]}
        defaultType="mobile"
        rows={rows}
        renderValueEditor={renderValueEditor}
        onCreate={onCreate}
        onUpdate={async () => {}}
        onDelete={async () => {}}
      />
    </MantineProvider>,
  );
  // Existing rows render their editor with autoFocus false, the add row with true.
  const existingRowRenders = () => renderValueEditor.mock.calls.filter(([, , autoFocus]) => !autoFocus).length;
  return { renderValueEditor, existingRowRenders };
}

describe("RepeatableRowGroup add row (KAN-167)", () => {
  it("typing in the add row does not re-render the existing rows' value editors", async () => {
    const user = userEvent.setup();
    const { existingRowRenders } = renderGroup();
    await user.click(screen.getByRole("button", { name: "+ Add phone" }));
    const before = existingRowRenders();

    await user.type(screen.getByRole("textbox", { name: "New value" }), "0791234567");

    expect(existingRowRenders()).toBe(before);
    expect(screen.getByRole("textbox", { name: "New value" })).toHaveProperty("value", "0791234567");
  });

  it("still saves the draft with its type, closes on success and starts the next draft empty", async () => {
    const user = userEvent.setup();
    const onCreate = vi.fn(async () => {});
    renderGroup(onCreate);
    await user.click(screen.getByRole("button", { name: "+ Add phone" }));
    await user.selectOptions(screen.getAllByRole("combobox").at(-1)!, "work");
    await user.type(screen.getByRole("textbox", { name: "New value" }), "0441234567");
    await user.click(screen.getByRole("button", { name: "Save" }));

    expect(onCreate).toHaveBeenCalledWith({ type: "work", value: "0441234567" });
    expect(screen.queryByRole("textbox", { name: "New value" })).toBeNull();

    await user.click(screen.getByRole("button", { name: "+ Add phone" }));
    expect(screen.getByRole("textbox", { name: "New value" })).toHaveProperty("value", "");
    expect((screen.getAllByRole("combobox").at(-1) as HTMLSelectElement).value).toBe("mobile");
  });

  it("keeps the draft and shows the error when the save is refused", async () => {
    const user = userEvent.setup();
    renderGroup(async () => {
      throw new Error("Not a valid number.");
    });
    await user.click(screen.getByRole("button", { name: "+ Add phone" }));
    await user.type(screen.getByRole("textbox", { name: "New value" }), "12");
    await user.click(screen.getByRole("button", { name: "Save" }));

    expect(await screen.findByText("Not a valid number.")).toBeTruthy();
    expect(screen.getByRole("textbox", { name: "New value" })).toHaveProperty("value", "12");
    expect(screen.getByRole("button", { name: "Save" })).toHaveProperty("disabled", false);
  });
});
