import type { CSSProperties, ReactNode } from "react";
import { Tooltip } from "@mantine/core";
import { Info } from "lucide-react";
import { slate } from "../tokens";

/**
 * A field's hint is an info icon beside its label (KAN-443; Anto,
 * 2026-10-25: "we want less text"): the hint shows in a tooltip on hover
 * and on keyboard focus of the icon, never as a line of text between label
 * and input or under it. The hint still reaches assistive technology: it
 * is rendered as the input's own `description` (so Mantine wires
 * `aria-describedby`), only visually hidden, and the icon is described by
 * the same text. The icon has no name of its own, so a label's accessible
 * name stays the label — `getByLabelText("Street")` keeps matching.
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

export interface FieldLabelProps {
  label: ReactNode;
  hint: ReactNode;
  /** The id of the element holding the hint text (the input's visually hidden description). */
  hintId?: string;
}

export function FieldLabel({ label, hint, hintId }: FieldLabelProps) {
  return (
    <span style={{ display: "inline-flex", alignItems: "center", gap: 4 }}>
      {label}
      <Tooltip label={hint} multiline maw={320} withArrow events={{ hover: true, focus: true, touch: true }} position="top-start">
        <span
          tabIndex={0}
          aria-describedby={hintId}
          data-field-hint
          style={{ display: "inline-flex", color: slate[5], cursor: "help", lineHeight: 0, borderRadius: 4 }}
        >
          <Info size={14} aria-hidden="true" />
        </span>
      </Tooltip>
    </span>
  );
}

export interface FieldHintProps {
  label: ReactNode;
  description?: ReactNode;
  descriptionProps?: { style?: CSSProperties };
}

/** A stable element id for a hint text: the same text gives the same id on
 * every render, so no hook is needed and `fieldHint` may be called inside a
 * condition (a field that only shows for one reference type, say). Two
 * fields with the same hint on one screen share the id and the icon of the
 * second describes the first's text — the same words. */
function hintIdFor(hint: ReactNode): string | undefined {
  if (typeof hint !== "string" && typeof hint !== "number") return undefined;
  const text = String(hint);
  let h = 0;
  for (let i = 0; i < text.length; i += 1) h = (h * 31 + text.charCodeAt(i)) | 0;
  return `field-hint-${(h >>> 0).toString(36)}`;
}

/**
 * The props a Mantine input takes for a hinted field:
 * `<TextInput {...fieldHint(t("x"), t("xHint"))} … />`. With no hint
 * (undefined, null, false or an empty string) the label is returned as is.
 * Not a hook: safe inside a condition.
 */
export function fieldHint(label: ReactNode, hint: ReactNode): FieldHintProps {
  if (hint === undefined || hint === null || hint === false || hint === "") return { label };
  const hintId = hintIdFor(hint);
  return {
    label: <FieldLabel label={label} hint={hint} hintId={hintId} />,
    // The hint text itself is the hidden element: Mantine's inline inputs
    // (Switch, Checkbox) render `description` but ignore `descriptionProps`.
    description: (
      <span id={hintId} style={VISUALLY_HIDDEN}>
        {hint}
      </span>
    ),
    descriptionProps: { style: VISUALLY_HIDDEN },
  };
}
