import type { CSSProperties, ReactNode } from "react";
import { Tooltip } from "@mantine/core";
import { Info } from "lucide-react";
import { radius, slate, spacing } from "../tokens";

/**
 * A field's hint is an info icon beside its label (KAN-443; Anto,
 * 2026-10-25: "we want less text"): the hint shows in a tooltip on hover
 * and on keyboard focus of the icon, never as a line of text between label
 * and input or under it. The hint still reaches assistive technology: it
 * stays the input's own `description` (so Mantine wires
 * `aria-describedby`), only visually hidden; the theme renders every
 * input's description after the input, so a hidden hint never moves the
 * input (`inputWrapperOrder`, theme.ts). The icon is named by the caller's
 * translated label — the kit ships no string — and never activates the
 * control whose label it sits in.
 */

/** Keeps an element for assistive technology only. */
export const VISUALLY_HIDDEN: CSSProperties = {
  position: "absolute",
  width: 1,
  height: 1,
  margin: -1,
  padding: 0,
  border: 0,
  overflow: "hidden",
  clip: "rect(0 0 0 0)",
  whiteSpace: "nowrap",
};

export interface FieldHintLabels {
  /** The icon's accessible name, translated ("Hinweis"). */
  icon: string;
}

export interface FieldLabelProps {
  label: NonNullable<ReactNode>;
  hint: ReactNode;
  labels: FieldHintLabels;
}

export function FieldLabel({ label, hint, labels }: FieldLabelProps) {
  return (
    <span style={{ display: "inline-flex", alignItems: "center", gap: spacing.xs }}>
      {label}
      <Tooltip label={hint} multiline maw={320} withArrow events={{ hover: true, focus: true, touch: true }} position="top-start">
        <span
          role="img"
          aria-label={labels.icon}
          tabIndex={0}
          data-field-hint
          // Inside a <label>: a click must not activate the control (a
          // Switch would flip, a text input would steal the focus).
          onClick={(event) => event.preventDefault()}
          style={{ display: "inline-flex", color: slate[5], cursor: "help", lineHeight: 0, borderRadius: radius.sm }}
        >
          <Info size={14} aria-hidden="true" />
        </span>
      </Tooltip>
    </span>
  );
}

export interface FieldHintProps {
  label: NonNullable<ReactNode>;
  description?: ReactNode;
}

/**
 * The props a Mantine input takes for a hinted field:
 * `<TextInput {...fieldHint(t("x"), t("xHint"), labels)} … />`. With no hint
 * (undefined, null, false or an empty string) the label is returned as is.
 * Not a hook: safe inside a condition. The description is the hint text in
 * a visually hidden span — Mantine's inline inputs (Switch, Checkbox)
 * render `description` but forward any other prop to the DOM.
 */
export function fieldHint(label: NonNullable<ReactNode>, hint: ReactNode, labels: FieldHintLabels): FieldHintProps {
  if (hint === undefined || hint === null || hint === false || hint === "") return { label };
  return {
    label: <FieldLabel label={label} hint={hint} labels={labels} />,
    description: <span style={VISUALLY_HIDDEN}>{hint}</span>,
  };
}
