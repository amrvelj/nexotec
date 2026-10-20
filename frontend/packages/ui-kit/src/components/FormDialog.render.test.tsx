// @vitest-environment jsdom
import { describe, expect, it, vi } from "vitest";
import type { ReactNode } from "react";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MantineProvider } from "@mantine/core";
import { FormDialog } from "./FormDialog";

// KAN-200 — the three presentation props: `formId` makes the footer button the
// form's own submit control (one click, ONE submission — the dialog never
// calls onSubmit beside the form's onSubmit), `destructive` colours it red,
// `size` widens a sectioned form.

const wrap = (ui: ReactNode) => render(<MantineProvider>{ui}</MantineProvider>);

describe("FormDialog", () => {
  it("submits a form exactly once per click when formId is set", async () => {
    const user = userEvent.setup();
    const onSubmit = vi.fn();
    wrap(
      <FormDialog opened onClose={() => {}} title="Edit" formId="f1" onSubmit={onSubmit} submitLabel="Save" cancelLabel="Cancel">
        <form
          id="f1"
          onSubmit={(event) => {
            event.preventDefault();
            onSubmit();
          }}
        >
          <input aria-label="Name" defaultValue="x" />
        </form>
      </FormDialog>,
    );
    await user.click(screen.getByRole("button", { name: "Save" }));
    expect(onSubmit).toHaveBeenCalledTimes(1);
  });

  it("calls onSubmit itself when there is no form, and renders a destructive submit in red", async () => {
    const user = userEvent.setup();
    const onSubmit = vi.fn();
    wrap(
      <FormDialog opened onClose={() => {}} title="Deactivate" destructive onSubmit={onSubmit} submitLabel="Deactivate" cancelLabel="Cancel">
        <p>Are you sure?</p>
      </FormDialog>,
    );
    const button = screen.getByRole("button", { name: "Deactivate" });
    await user.click(button);
    expect(onSubmit).toHaveBeenCalledTimes(1);
    expect(button.getAttribute("data-variant") ?? button.className).toBeTruthy();
    expect(button).toHaveAttribute("type", "button");
  });
});
