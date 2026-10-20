import { type ReactNode } from "react";
import { Modal, Button, Group, Stack, Alert } from "@mantine/core";
import { AlertTriangle } from "lucide-react";
import { purple, slate } from "../tokens";

export interface FormDialogExistingRecordNotice {
  /** FR-V-15 / § Shared forms: "a unique key that already exists is not
   * a validation error — it is the same record." Rendered instead of a
   * field-level error, offering to open what's already there rather than
   * teaching the user to work around a rejection. */
  message: string;
  openLabel: string;
  onOpenExisting: () => void;
}

export interface FormDialogProps {
  opened: boolean;
  onClose: () => void;
  title: string;
  children: ReactNode;
  onSubmit: () => void | Promise<void>;
  submitLabel: string;
  cancelLabel: string;
  submitting?: boolean;
  submitDisabled?: boolean;
  existingRecordNotice?: FormDialogExistingRecordNotice | null;
  /** `md` (the default) for a short form; `lg` for a sectioned one, such
   * as the user form's six sections (KAN-200). Never wider: a form that
   * needs more is a detail screen. */
  size?: "md" | "lg";
  /** A dialog whose submit is irreversible (a deactivation, KAN-200)
   * renders its primary action in the destructive tone — the same chrome,
   * the same footer, the colour saying what the label says. */
  destructive?: boolean;
  /** An element id a `<form>` among the children carries: the submit button
   * becomes that form's submit control (Enter in a field submits too) and
   * the form's own `onSubmit` is the one path to `onSubmit` — the dialog
   * does not call it itself then. */
  formId?: string;
}

/**
 * § Component Contracts — Shared forms. "The create form IS the edit
 * form." This component only owns the dialog chrome (title, footer,
 * submit/cancel, the existing-record notice) — the field set itself is
 * the caller's `children`, so the SAME field list renders whether this
 * is opened as "create" or reused as an inline "edit" surface elsewhere.
 * Live duplicate detection on identifying fields is the caller's own
 * concern (it knows which fields are identifying); this dialog only
 * renders whatever the caller decides to show for it.
 */
export function FormDialog({
  opened,
  onClose,
  title,
  children,
  onSubmit,
  submitLabel,
  cancelLabel,
  submitting,
  submitDisabled,
  existingRecordNotice,
  size = "md",
  destructive = false,
  formId,
}: FormDialogProps) {
  return (
    <Modal opened={opened} onClose={onClose} title={title} size={size}>
      <Stack gap="md">
        {children}

        {existingRecordNotice && (
          <Alert icon={<AlertTriangle size={16} />} color="yellow" styles={{ root: { borderColor: slate[2] } }}>
            <Stack gap="xs">
              <span>{existingRecordNotice.message}</span>
              <Button
                variant="subtle"
                size="xs"
                color="grape"
                onClick={existingRecordNotice.onOpenExisting}
                style={{ alignSelf: "flex-start", color: purple[7] }}
              >
                {existingRecordNotice.openLabel}
              </Button>
            </Stack>
          </Alert>
        )}

        <Group justify="flex-end">
          <Button variant="default" onClick={onClose} disabled={submitting}>
            {cancelLabel}
          </Button>
          <Button
            // With a form, the button is that form's submit control and the
            // form's own onSubmit runs — never both paths (one click, one save).
            onClick={formId ? undefined : () => void onSubmit()}
            loading={submitting}
            disabled={submitDisabled}
            color={destructive ? "red" : undefined}
            type={formId ? "submit" : "button"}
            form={formId}
          >
            {submitLabel}
          </Button>
        </Group>
      </Stack>
    </Modal>
  );
}
