// @vitest-environment jsdom
import { afterEach, describe, expect, it } from "vitest";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { Checkbox, MantineProvider, Switch, TextInput } from "@mantine/core";
import { fieldHint } from "./FieldHint";
import { testTheme } from "../test/testTheme";

// KAN-443 — a field's hint is an info icon beside the label: the tooltip
// shows the hint on hover and on keyboard focus, the input is still
// described by the hint for assistive technology, the hint is not a visible
// line of text, and the icon never activates the control whose label it
// sits in. The named icon sits inside the <label>, so it joins the input's
// accessible name ("Rue Indication"); `getByLabelText("Rue")` matches the
// label's text, not that name.

const LABELS = { icon: "Indication" };

function Field({ hint }: { hint?: string }) {
  return (
    <MantineProvider theme={testTheme}>
      <TextInput {...fieldHint("Rue", hint, LABELS)} />
    </MantineProvider>
  );
}

// Mantine opens a tooltip on focus only under `:focus-visible`, which jsdom
// does not implement: a keyboard focus is made visible here.
const nativeMatches = Element.prototype.matches;
afterEach(() => {
  Element.prototype.matches = nativeMatches;
});

describe("fieldHint", () => {
  it("puts the hint in a tooltip on the icon and keeps the input described by it", async () => {
    const user = userEvent.setup();
    const { container } = render(<Field hint="Comme au registre du commerce." />);
    const input = screen.getByLabelText("Rue");
    expect(input).toHaveAccessibleDescription("Comme au registre du commerce.");
    const icon = screen.getByRole("img", { name: "Indication" });
    // Not a visible line of text: the description is visually hidden, and it
    // sits after the input (theme), so the input is not pushed down.
    const description = document.getElementById(input.getAttribute("aria-describedby")!)!;
    expect(description.firstElementChild).toHaveStyle({ position: "absolute", width: "1px" });
    expect(input.compareDocumentPosition(description) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
    // Mantine sets the margin variable on the wrapper when a description
    // precedes the input; the theme's order leaves no element carrying it.
    expect(container.querySelector('[style*="--input-margin-top"]')).toBeNull();
    expect(screen.queryByRole("tooltip")).toBeNull();
    await user.hover(icon);
    expect(await screen.findByRole("tooltip")).toHaveTextContent("Comme au registre du commerce.");
    await user.unhover(icon);
  });

  it("opens the tooltip on keyboard focus; the icon is the tab stop before the input", async () => {
    // jsdom has no `:focus-visible`; Mantine opens the tooltip on focus only under it.
    Element.prototype.matches = function (this: Element, selector: string) {
      return selector === ":focus-visible" ? document.activeElement === this : nativeMatches.call(this, selector);
    } as typeof Element.prototype.matches;
    const user = userEvent.setup();
    render(<Field hint="Comme au registre du commerce." />);
    await user.tab();
    expect(screen.getByRole("img", { name: "Indication" })).toHaveFocus();
    expect(await screen.findByRole("tooltip")).toHaveTextContent("Comme au registre du commerce.");
    await user.tab();
    expect(screen.getByLabelText("Rue")).toHaveFocus();
  });

  it("renders a plain label when there is no hint", () => {
    render(<Field />);
    expect(screen.getByLabelText("Rue")).toBeInTheDocument();
    expect(screen.queryByRole("img")).toBeNull();
    expect(screen.getByLabelText("Rue")).not.toHaveAttribute("aria-describedby");
  });

  it("never activates the control whose label it sits in: a Switch stays as it was", async () => {
    const user = userEvent.setup();
    render(
      <MantineProvider theme={testTheme}>
        <Switch {...fieldHint("Gérant", "Administre les utilisateurs.", LABELS)} defaultChecked={false} />
      </MantineProvider>,
    );
    const toggle = screen.getByRole("switch");
    expect(toggle).not.toBeChecked();
    await user.click(screen.getByRole("img", { name: "Indication" }));
    expect(toggle).not.toBeChecked();
    // The hint is on screen only in the tooltip: the description span is hidden.
    expect(screen.getByText("Administre les utilisateurs.", { selector: "span" })).toHaveStyle({ position: "absolute", width: "1px" });
  });
  it("links the hint to a Checkbox through descriptionId, which Mantine does not do itself", () => {
    render(
      <MantineProvider theme={testTheme}>
        <Checkbox {...fieldHint("Compte par défaut", "Proposé sur chaque facture.", LABELS, { descriptionId: "default-hint" })} />
      </MantineProvider>,
    );
    const checkbox = screen.getByRole("checkbox");
    expect(checkbox).toHaveAttribute("aria-describedby", "default-hint");
    expect(checkbox).toHaveAccessibleDescription("Proposé sur chaque facture.");
    expect(document.getElementById("default-hint")).toHaveStyle({ position: "absolute", width: "1px" });
  });
});
