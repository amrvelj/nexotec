import { useEffect, useState } from "react";
import { Alert, Button, Chip, Group, Modal, Stack, Text, Textarea } from "@mantine/core";
import { AlertTriangle, Send } from "lucide-react";
import { slate } from "../tokens";

/** One channel the dialog offers. The caller passes only the channels the
 * Betrieb can send on (FR-A-52, «connected channels only»), each with its
 * labels already translated. */
export interface SendMessageChannel {
  /** The channel's code (`email`, `sms`, `whatsapp`). */
  channel: string;
  /** The chip's label («E-Mail», «SMS», «WhatsApp»). */
  label: string;
  /** Where the message goes, as shown («An: anna@example.ch»); null hides the line. */
  recipientLine: string | null;
  /** A short line under the text: what the message carries (the PDF
   * attached, a secure link added) and from whom it leaves. */
  note?: string | null;
  /** The text the send carries today (the Betrieb's standard text in the
   * customer's language); null when there is none. */
  text: string | null;
  /** False for a text the provider fixed (WhatsApp's approved template). */
  textEditable: boolean;
  /** Why this channel cannot send now (a consent refusal with its reason, a
   * template not approved, no text), translated; null when the send may go. */
  blockedMessage: string | null;
}

export interface SendMessageDialogLabels {
  title: string;
  channels: string;
  text: string;
  /** Said under a fixed text (WhatsApp sends its approved template as it is). */
  textFixed: string;
  /** Said when no channel is offered at all (nothing connected). */
  noChannel: string;
  /** Said while no channel is chosen yet. */
  chooseChannel: string;
  send: string;
  cancel: string;
}

export interface SendMessageDialogProps {
  opened: boolean;
  onClose: () => void;
  /** The recipient's name, shown above the channels. */
  recipientName: string;
  channels: SendMessageChannel[];
  /** The channel the dialog opens on (the customer's preferred one, when it
   * is offered); null opens with none chosen. */
  preselectedChannel: string | null;
  /** Sends on `channel`. `text` is the author's own wording and is passed
   * only when the author changed the text — an unchanged text is never
   * sent back, so the server sends its own (KAN-217 PR-5's rule). */
  onSend: (channel: string, text: string | undefined) => void | Promise<void>;
  sending?: boolean;
  /** The send's refusal, translated; shown above the footer. */
  error?: string | null;
  labels: SendMessageDialogLabels;
}

/**
 * KAN-217 PR-6 (D-A-41, FR-A-51–54, A-RULE-10) — the one send dialog every
 * module opens for a document or a reminder; no module builds its own.
 * Channel chips (connected channels only), the preferred one pre-selected,
 * the text as it will be sent, a refusal with its reason, and send. The
 * dialog owns no wording and calls nothing: the module reads the options and
 * runs the send (its route), and passes both in.
 */
export function SendMessageDialog({
  opened,
  onClose,
  recipientName,
  channels,
  preselectedChannel,
  onSend,
  sending = false,
  error = null,
  labels,
}: SendMessageDialogProps) {
  const offered = (code: string | null) => (code != null && channels.some((c) => c.channel === code) ? code : null);
  const [selected, setSelected] = useState<string | null>(offered(preselectedChannel));
  // The author's draft per channel; absent = the text as the server sends it.
  const [drafts, setDrafts] = useState<Record<string, string>>({});

  useEffect(() => {
    if (opened) {
      setSelected(offered(preselectedChannel));
      setDrafts({});
    }
    // Reset on each opening only; the options may refresh while it is open.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [opened]);

  const current = channels.find((c) => c.channel === selected) ?? null;
  const shownText = current ? (drafts[current.channel] ?? current.text ?? "") : "";
  const edited = current != null && current.textEditable && current.channel in drafts && drafts[current.channel] !== (current.text ?? "");
  const canSend = current != null && current.blockedMessage == null && shownText.trim() !== "" && !sending;

  const send = () => {
    if (!current || !canSend) return;
    void onSend(current.channel, edited ? drafts[current.channel] : undefined);
  };

  return (
    <Modal opened={opened} onClose={onClose} title={labels.title} size="lg">
      <Stack gap="md">
        <Text fw={600}>{recipientName}</Text>

        {channels.length === 0 ? (
          <Alert icon={<AlertTriangle size={16} />} color="yellow" styles={{ root: { borderColor: slate[2] } }}>
            {labels.noChannel}
          </Alert>
        ) : (
          <Stack gap={6}>
            <Text size="sm" fw={500} id="send-dialog-channels">
              {labels.channels}
            </Text>
            <Chip.Group multiple={false} value={selected ?? ""} onChange={(value) => setSelected(value || null)}>
              <Group gap="xs" role="radiogroup" aria-labelledby="send-dialog-channels">
                {channels.map((c) => (
                  <Chip key={c.channel} value={c.channel} variant="outline">
                    {c.label}
                  </Chip>
                ))}
              </Group>
            </Chip.Group>
          </Stack>
        )}

        {channels.length > 0 && current == null && (
          <Text size="sm" c="dimmed">
            {labels.chooseChannel}
          </Text>
        )}

        {current && (
          <Stack gap="xs">
            {current.recipientLine && <Text size="sm">{current.recipientLine}</Text>}
            {current.blockedMessage && (
              <Alert icon={<AlertTriangle size={16} />} color="red" styles={{ root: { borderColor: slate[2] } }}>
                {current.blockedMessage}
              </Alert>
            )}
            <Textarea
              label={labels.text}
              value={shownText}
              readOnly={!current.textEditable}
              description={current.textEditable ? undefined : labels.textFixed}
              autosize
              minRows={5}
              maxRows={14}
              onChange={(event) => {
                const value = event.currentTarget.value;
                setDrafts((previous) => ({ ...previous, [current.channel]: value }));
              }}
            />
            {current.note && (
              <Text size="xs" c="dimmed">
                {current.note}
              </Text>
            )}
          </Stack>
        )}

        {error && (
          <Alert color="red" styles={{ root: { borderColor: slate[2] } }}>
            {error}
          </Alert>
        )}

        <Group justify="flex-end" gap="sm">
          <Button variant="default" onClick={onClose}>
            {labels.cancel}
          </Button>
          <Button leftSection={<Send size={14} />} loading={sending} disabled={!canSend} onClick={send}>
            {labels.send}
          </Button>
        </Group>
      </Stack>
    </Modal>
  );
}
