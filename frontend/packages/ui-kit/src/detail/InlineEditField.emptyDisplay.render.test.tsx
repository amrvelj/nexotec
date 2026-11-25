// @vitest-environment jsdom
import { describe, expect, it } from "vitest";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MantineProvider } from "@mantine/core";
import { InlineEditField } from "./InlineEditField";
import { testTheme } from "../test/testTheme";

// ADR-220 (KAN-805): a caller whose empty state carries meaning draws it with
// `emptyDisplay`; without it, empty reads as the italic empty-field label.

function field(emptyDisplay?: React.ReactNode) {
  render(
    <MantineProvider theme={testTheme}>
      <InlineEditField
        value=""
        isEmpty
        emptyLabel="Nicht gesetzt"
        emptyDisplay={emptyDisplay}
        onSave={async () => {}}
        describeError={() => "x"}
      />
    </MantineProvider>,
  );
}

describe("InlineEditField's empty display (ADR-220)", () => {
  it("draws the caller's empty display instead of the italic label, and still opens for editing", async () => {
    field(<span data-testid="badge">Nicht gesetzt</span>);
    const badge = screen.getByTestId("badge");
    expect(badge.style.fontStyle).toBe("");
    await userEvent.setup().click(badge);
    expect(screen.getByRole("textbox")).toBeInTheDocument();
  });

  it("without one, empty is the italic label", () => {
    field();
    expect(screen.getByText("Nicht gesetzt").style.fontStyle).toBe("italic");
  });
});
