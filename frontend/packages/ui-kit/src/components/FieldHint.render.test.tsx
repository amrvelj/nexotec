// @vitest-environment jsdom
import { describe, expect, it } from "vitest";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MantineProvider, TextInput } from "@mantine/core";
import { fieldHint } from "./FieldHint";

// KAN-443 — a field's hint is an info icon beside the label: the tooltip
// shows the hint on keyboard focus, the input is still described by the
// hint for assistive technology, the hint is not a visible line of text, and
// the label's accessible name is the label alone.

function Field({ hint }: { hint?: string }) {
  return (
    <MantineProvider>
      <TextInput {...fieldHint("Rue", hint)} />
    </MantineProvider>
  );
}

describe("fieldHint", () => {
  it("puts the hint in a tooltip on the icon and keeps the input described by it", async () => {
    const user = userEvent.setup();
    const { container } = render(<Field hint="Comme au registre du commerce." />);
    const input = screen.getByLabelText("Rue");
    expect(input).toHaveAccessibleDescription("Comme au registre du commerce.");
    const icon = container.querySelector("[data-field-hint]") as HTMLElement;
    expect(icon).not.toBeNull();
    // Not a visible line of text: the description is visually hidden.
    const description = document.getElementById(icon.getAttribute("aria-describedby")!)!;
    expect(description).toHaveStyle({ position: "absolute", width: "1px" });
    expect(screen.queryByRole("tooltip")).toBeNull();
    await user.hover(icon);
    expect(await screen.findByRole("tooltip")).toHaveTextContent("Comme au registre du commerce.");
    await user.unhover(icon);
    // The icon sits in the label, so it is the first tab stop; the input
    // follows. The tooltip opens on keyboard focus too (`events.focus`), but
    // Mantine gates that on `:focus-visible`, which jsdom does not implement —
    // the focus path is checked in the browser, not here.
    await user.tab();
    expect(icon).toHaveFocus();
    await user.tab();
    expect(input).toHaveFocus();
  });

  it("renders a plain label when there is no hint", () => {
    const { container } = render(<Field />);
    expect(screen.getByLabelText("Rue")).toBeInTheDocument();
    expect(container.querySelector("[data-field-hint]")).toBeNull();
    expect(screen.getByLabelText("Rue")).not.toHaveAttribute("aria-describedby");
  });
});
