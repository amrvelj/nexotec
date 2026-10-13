import { useState, type ReactNode } from "react";
import { NumberInput, type NumberInputProps } from "@mantine/core";
import type { EditorRenderProps } from "../detail/InlineEditField";

// KAN-168, ADR-076: every CHF amount reads `CHF 12'500.00`, inputs included
// (Anto, 2026-10-06). This is the one CHF input; the display formatter beside
// it (`formatCurrencyChf`, swissNumber.ts) is the one CHF output. Both group
// with the ASCII apostrophe and use a period decimal, in all four languages —
// no locale parameter, exactly like the printed documents.
//
// While the field has focus it shows the grouping but not the padding
// (`CHF 12'500` as you type `12500`), so the caret never fights two forced
// zeros; on blur it fixes two decimals (`CHF 12'500.00`).
//
// What it emits is unchanged from a bare NumberInput: a plain number (or ''
// for an empty field), never the formatted text — so no request body
// changes. A caller holding a decimal string (an API DECIMAL, an inline
// editor's draft) may pass it as `value`; it is read as the number it
// spells, and the caller turns the emitted number back into a string.
//
// Never for a quantity, a percentage, months or kilometres — those stay
// NumberInputs (apps/dms/src/architecture/no-bare-money-number-input.test.ts
// names every one).

export const CHF_INPUT_PREFIX = "CHF ";

export type ChfInputProps = Omit<
  NumberInputProps,
  "prefix" | "suffix" | "thousandSeparator" | "decimalSeparator" | "decimalScale" | "fixedDecimalScale" | "allowDecimal"
>;

function asNumber(value: NumberInputProps["value"]): NumberInputProps["value"] {
  if (typeof value !== "string" || value.trim() === "") return value;
  const parsed = Number(value);
  return Number.isNaN(parsed) ? value : parsed;
}

export function ChfInput({ value, defaultValue, onFocus, onBlur, ...props }: ChfInputProps) {
  const [focused, setFocused] = useState(false);
  return (
    <NumberInput
      {...props}
      value={asNumber(value)}
      defaultValue={asNumber(defaultValue)}
      prefix={CHF_INPUT_PREFIX}
      thousandSeparator="'"
      decimalSeparator="."
      decimalScale={2}
      fixedDecimalScale={!focused}
      onFocus={(event) => {
        setFocused(true);
        onFocus?.(event);
      }}
      onBlur={(event) => {
        setFocused(false);
        onBlur?.(event);
      }}
    />
  );
}

/**
 * The `renderEditor` for an InlineEditField holding a CHF amount. The editor
 * exists only while it has focus, so it shows the grouping, never the API's
 * raw `42500.00` (`CHF 42'500` as it opens; `CHF 41'900.5` for 41900.50) —
 * the two fixed decimals are the display value's, `formatCurrencyChf`, once
 * the edit ends. The draft stays a decimal string (`"12500"` after typing
 * 12500; untouched, the API's own `"42500.00"`), so `onSave` receives what it
 * received from a plain text editor.
 */
export function chfInlineEditor(props: Partial<ChfInputProps> = {}): (editor: EditorRenderProps) => ReactNode {
  return function ChfInlineEditor({ value, onChange, onKeyDown, onBlur, autoFocus }: EditorRenderProps) {
    return (
      <ChfInput
        size="xs"
        {...props}
        value={value}
        autoFocus={autoFocus}
        onChange={(next) => onChange(next === "" ? "" : String(next))}
        onKeyDown={onKeyDown}
        onBlur={() => onBlur()}
      />
    );
  };
}
