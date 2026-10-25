import { useId, type ReactNode } from "react";
import { Textarea } from "@mantine/core";
import { semantic, slate, typography } from "../tokens";
import { formatNumber } from "../format/swissNumber";

export interface CounterTextareaProps {
  /** The label, or `fieldHint(label, hint, labels)`'s label (KAN-443). */
  label: NonNullable<ReactNode>;
  value: string;
  onChange: (value: string) => void;
  maxLength: number;
  /** e.g. "Auf der Ergebnisliste werden 125 Zeichen angezeigt." — the
   * marketplace's own display limit is often shorter than the stored
   * field's own max length (AS24's zusatztitel stores 500, shows 125). */
  displayLimitCaption?: string;
  /** What `fieldHint()` returns: the hint reaches the textarea's aria-describedby. */
  description?: ReactNode;
  minRows?: number;
  /** Lets a host that saves on a pause also save when the field is left. */
  onBlur?: () => void;
}

/**
 * § Publishing tab — every listing-text field carries a live "0 / 500"
 * counter. One ui-kit gap this package's own research surfaced (no
 * char-counting text input existed anywhere in the library) — added here
 * rather than built inline in the stock package.
 */
export function CounterTextarea({
  label,
  value,
  onChange,
  maxLength,
  displayLimitCaption,
  description,
  minRows = 2,
  onBlur,
}: CounterTextareaProps) {
  // KAN-425: the label names the field (a <label for>), so assistive
  // technology announces it and tests find it by its label.
  const id = useId();
  const overLimit = value.length > maxLength;
  return (
    <div>
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "baseline" }}>
        <label
          htmlFor={id}
          style={{ fontSize: typography.label.size, fontWeight: typography.label.weight, color: slate[7] }}
        >
          {label}
        </label>
        <span style={{ fontSize: typography.meta.size, color: overLimit ? semantic.destructive.text : slate[5] }}>
          {formatNumber(value.length)} / {formatNumber(maxLength)}
        </span>
      </div>
      <Textarea
        id={id}
        value={value}
        onChange={(e) => onChange(e.currentTarget.value)}
        onBlur={onBlur}
        minRows={minRows}
        description={description}
        error={overLimit || undefined}
      />
      {displayLimitCaption && (
        <span style={{ fontSize: typography.meta.size, color: slate[5] }}>{displayLimitCaption}</span>
      )}
    </div>
  );
}
