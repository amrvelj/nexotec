// @vitest-environment jsdom
import { describe, expect, it, vi } from "vitest";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MantineProvider } from "@mantine/core";
import { InlineEditField } from "./InlineEditField";

// KAN-473 — the field showed the `message` of whatever its onSave threw (an
// ApiError's is the API's English text) and its own English conflict, failure
// and reload wording. It says the caller's sentence now, and the caller's
// reload label; nothing of the error's own text reaches the screen.

class RefusalWithEnglishMessage extends Error {
  readonly status: number;
  constructor(status: number) {
    super("This phone number is already on this customer.");
    this.status = status;
  }
}

function renderField(err: unknown, opts: { conflict?: boolean; onReload?: () => void } = {}) {
  const onSave = vi.fn(async (_raw: string) => {
    throw err;
  });
  const describeError = vi.fn((e: unknown) =>
    e instanceof RefusalWithEnglishMessage && e.status === 409 ? "Ce numéro figure déjà chez ce client." : "La modification n'a pas pu être enregistrée.",
  );
  render(
    <MantineProvider>
      {opts.onReload ? (
        <InlineEditField
          value="079 123 45 67"
          onSave={onSave}
          describeError={describeError}
          isConflict={() => opts.conflict ?? false}
          onReload={opts.onReload}
          reloadLabel="Recharger"
        />
      ) : (
        <InlineEditField value="079 123 45 67" onSave={onSave} describeError={describeError} />
      )}
    </MantineProvider>,
  );
  return { onSave, describeError };
}

async function editAndSave() {
  const user = userEvent.setup();
  await user.click(screen.getByText("079 123 45 67"));
  await user.type(screen.getByRole("textbox"), "8{Enter}");
  return user;
}

describe("InlineEditField says a refused save in the caller's words (KAN-473)", () => {
  it("shows describeError's sentence for the thrown error, never the error's own message", async () => {
    const err = new RefusalWithEnglishMessage(409);
    const { describeError } = renderField(err);
    await editAndSave();

    expect(await screen.findByText("Ce numéro figure déjà chez ce client.")).toBeInTheDocument();
    expect(describeError).toHaveBeenCalledWith(err);
    expect(screen.queryByText(/already on this customer/)).not.toBeInTheDocument();
    // Still editing, so what was typed is not thrown away.
    expect(screen.getByRole("textbox")).toHaveValue("079 123 45 678");
  });

  it("says a thrown non-Error through describeError too, with no English fallback", async () => {
    renderField("boom");
    await editAndSave();

    expect(await screen.findByText("La modification n'a pas pu être enregistrée.")).toBeInTheDocument();
    expect(screen.queryByText("Failed to save.")).not.toBeInTheDocument();
  });

  it("offers the reload on a conflict under the caller's label, beside the caller's sentence", async () => {
    const onReload = vi.fn();
    renderField(new RefusalWithEnglishMessage(409), { conflict: true, onReload });
    const user = await editAndSave();

    expect(await screen.findByText("Ce numéro figure déjà chez ce client.")).toBeInTheDocument();
    expect(screen.queryByText("Someone else changed this in the meantime.")).not.toBeInTheDocument();
    expect(screen.queryByText("Reload")).not.toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Recharger" }));
    expect(onReload).toHaveBeenCalledTimes(1);
  });

  it("offers no reload when the refusal is not a conflict", async () => {
    renderField(new RefusalWithEnglishMessage(400), { conflict: false, onReload: () => {} });
    await editAndSave();

    expect(await screen.findByText("La modification n'a pas pu être enregistrée.")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Recharger" })).not.toBeInTheDocument();
  });
});
