// @vitest-environment jsdom
import { describe, expect, it, vi } from "vitest";
import type { ReactNode } from "react";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MantineProvider } from "@mantine/core";
import { SendMessageDialog, type SendMessageChannel, type SendMessageDialogLabels } from "./SendMessageDialog";
import { testTheme } from "../test/testTheme";

// KAN-217 PR-6 — the one send dialog: chips of the offered channels, the
// preferred one pre-selected, the text as it will be sent, a refusal with its
// reason, and a send that passes the author's text only when they changed it.

const wrap = (ui: ReactNode) => render(<MantineProvider theme={testTheme}>{ui}</MantineProvider>);

const LABELS: SendMessageDialogLabels = {
  title: "Envoyer le document",
  channels: "Canal",
  text: "Texte",
  textFixed: "Modèle approuvé",
  noChannel: "Aucun canal connecté",
  chooseChannel: "Choisissez un canal",
  send: "Envoyer",
  cancel: "Annuler",
};

const EMAIL: SendMessageChannel = {
  channel: "email", label: "E-mail", recipientLine: "À : anna@example.ch", note: "PDF joint",
  text: "Madame, voici votre offre.", textEditable: true, blockedMessage: null,
};
const SMS: SendMessageChannel = {
  channel: "sms", label: "SMS", recipientLine: null, text: "Votre offre", textEditable: true,
  blockedMessage: "Aucun numéro de mobile utilisable",
};
const WHATSAPP: SendMessageChannel = {
  channel: "whatsapp", label: "WhatsApp", recipientLine: null, text: "Madame, votre offre", textEditable: false, blockedMessage: null,
};

function dialog(overrides: Partial<Parameters<typeof SendMessageDialog>[0]> = {}) {
  const onSend = vi.fn();
  wrap(
    <SendMessageDialog
      opened
      onClose={() => {}}
      recipientName="Anna Muster"
      channels={[EMAIL, SMS, WHATSAPP]}
      preselectedChannel="email"
      onSend={onSend}
      labels={LABELS}
      {...overrides}
    />,
  );
  return onSend;
}

describe("SendMessageDialog", () => {
  it("opens on the preselected channel with its text, and an unchanged text is not sent back", async () => {
    const user = userEvent.setup();
    const onSend = dialog();
    expect(screen.getByRole("textbox", { name: "Texte" })).toHaveValue("Madame, voici votre offre.");
    expect(screen.getByText("À : anna@example.ch")).toBeInTheDocument();
    expect(screen.getByText("PDF joint")).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Envoyer" }));
    expect(onSend).toHaveBeenCalledWith("email", undefined);
  });

  it("passes the author's own wording when they changed the text", async () => {
    const user = userEvent.setup();
    const onSend = dialog();
    const text = screen.getByRole("textbox", { name: "Texte" });
    await user.clear(text);
    await user.type(text, "Bonjour Anna");
    await user.click(screen.getByRole("button", { name: "Envoyer" }));
    expect(onSend).toHaveBeenCalledWith("email", "Bonjour Anna");
  });

  it("an edit typed back to the original text counts as unchanged", async () => {
    const user = userEvent.setup();
    const onSend = dialog({ channels: [{ ...EMAIL, text: "Hallo" }] });
    const text = screen.getByRole("textbox", { name: "Texte" });
    await user.type(text, "!");
    await user.type(text, "{backspace}");
    await user.click(screen.getByRole("button", { name: "Envoyer" }));
    expect(onSend).toHaveBeenCalledWith("email", undefined);
  });

  it("a refused channel says why and cannot be sent", async () => {
    const user = userEvent.setup();
    const onSend = dialog();
    await user.click(screen.getByText("SMS"));
    expect(screen.getByText("Aucun numéro de mobile utilisable")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Envoyer" })).toBeDisabled();
    await user.click(screen.getByRole("button", { name: "Envoyer" }));
    expect(onSend).not.toHaveBeenCalled();
  });

  it("a fixed text is read-only, says so, and is sent without a text of its own", async () => {
    const user = userEvent.setup();
    const onSend = dialog();
    await user.click(screen.getByText("WhatsApp"));
    const text = screen.getByRole("textbox", { name: "Texte" });
    expect(text).toHaveAttribute("readonly");
    expect(screen.getByText("Modèle approuvé")).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Envoyer" }));
    expect(onSend).toHaveBeenCalledWith("whatsapp", undefined);
  });

  it("without a preselected channel nothing is chosen and send waits", () => {
    dialog({ preselectedChannel: null });
    expect(screen.getByText("Choisissez un canal")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Envoyer" })).toBeDisabled();
  });

  it("a preselected channel that is not offered opens with none chosen", () => {
    dialog({ preselectedChannel: "whatsapp", channels: [EMAIL] });
    expect(screen.getByText("Choisissez un canal")).toBeInTheDocument();
  });

  it("with no channel connected it says so", () => {
    dialog({ channels: [], preselectedChannel: null });
    expect(screen.getByText("Aucun canal connecté")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Envoyer" })).toBeDisabled();
  });

  it("shows the send's refusal", () => {
    dialog({ error: "Refusé" });
    expect(screen.getByText("Refusé")).toBeInTheDocument();
  });
});
