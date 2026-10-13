// @vitest-environment jsdom
import { describe, expect, it, vi } from "vitest";
import { useState } from "react";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MantineProvider } from "@mantine/core";
import { ChfInput, chfInlineEditor } from "./ChfInput";
import { InlineEditField } from "../detail/InlineEditField";

// KAN-168 / ADR-076: a CHF input reads like every other CHF amount —
// `CHF 12'500.00` — and still emits the plain number.

function Controlled({ initial, onChange }: { initial: number | string; onChange: (v: number | string) => void }) {
  const [value, setValue] = useState<number | string>(initial);
  return (
    <ChfInput
      label="Price"
      value={value}
      onChange={(v) => {
        setValue(v);
        onChange(v);
      }}
    />
  );
}

function renderInput(initial: number | string = "") {
  const onChange = vi.fn();
  render(
    <MantineProvider>
      <Controlled initial={initial} onChange={onChange} />
      <button type="button">elsewhere</button>
    </MantineProvider>,
  );
  return { input: screen.getByLabelText("Price") as HTMLInputElement, onChange };
}

describe("ChfInput (KAN-168)", () => {
  it("typing 12500 shows CHF 12'500.00 after blur and emits 12500", async () => {
    const user = userEvent.setup();
    const { input, onChange } = renderInput();

    await user.click(input);
    await user.keyboard("12500");
    expect(input.value).toBe("CHF 12'500");

    await user.click(screen.getByRole("button", { name: "elsewhere" }));
    expect(input.value).toBe("CHF 12'500.00");
    expect(onChange).toHaveBeenLastCalledWith(12500);
    expect(onChange.mock.calls.every(([v]) => typeof v === "number")).toBe(true);
  });

  it("an API decimal string opens formatted, never as the raw 42500.00", () => {
    const { input } = renderInput("42500.00");

    expect(input.value).toBe("CHF 42'500.00");
  });

  it("a number with cents keeps them; a third decimal cannot be typed", async () => {
    const user = userEvent.setup();
    const { input, onChange } = renderInput();

    await user.click(input);
    await user.keyboard("1234.567");
    await user.click(screen.getByRole("button", { name: "elsewhere" }));

    expect(input.value).toBe("CHF 1'234.56");
    expect(onChange).toHaveBeenLastCalledWith(1234.56);
  });

  it("an empty field stays empty and emits ''", async () => {
    const user = userEvent.setup();
    const { input, onChange } = renderInput(250);
    expect(input.value).toBe("CHF 250.00");

    await user.clear(input);
    await user.click(screen.getByRole("button", { name: "elsewhere" }));

    expect(input.value).toBe("");
    expect(onChange).toHaveBeenLastCalledWith("");
  });
});

describe("chfInlineEditor (KAN-168)", () => {
  function renderField(editValue: string) {
    const onSave = vi.fn(async (_raw: string) => {});
    render(
      <MantineProvider>
        <InlineEditField value="shown" editValue={editValue} onSave={onSave} renderEditor={chfInlineEditor()} />
      </MantineProvider>,
    );
    return { onSave };
  }

  it("opens on the grouped CHF amount, never the API's raw 42500.00", async () => {
    const user = userEvent.setup();
    renderField("42500.00");

    await user.click(screen.getByText("shown"));

    const input = screen.getByRole("textbox") as HTMLInputElement;
    expect(input.value).toMatch(/^CHF 42'500/);
    expect(input.value).not.toContain("42500");
  });

  it("saves what was typed as a plain decimal string", async () => {
    const user = userEvent.setup();
    const { onSave } = renderField("42500.00");

    await user.click(screen.getByText("shown"));
    const input = screen.getByRole("textbox");
    await user.clear(input);
    await user.type(input, "12500.5{Enter}");

    expect(onSave).toHaveBeenCalledWith("12500.5");
  });
});
