import {
  Autocomplete,
  CheckboxGroup,
  FileInput,
  MultiSelect,
  NumberInput,
  PasswordInput,
  RadioGroup,
  Select,
  TagsInput,
  Textarea,
  TextInput,
  createTheme,
  type MantineColorsTuple,
} from "@mantine/core";
import { fontFamily, purple, radius, slate } from "./tokens";

const INPUT_WRAPPER_ORDER: ("label" | "input" | "description" | "error")[] = ["label", "input", "description", "error"];

const purpleTuple = purple as unknown as MantineColorsTuple;
const slateTuple = slate as unknown as MantineColorsTuple;

/**
 * The shared Mantine theme every Nexotec app renders with. "Design
 * principle above all others: density over decoration" — this is the one
 * place that principle gets encoded as actual defaults (compact-leaning
 * radius, no oversized default spacing) rather than left to each screen.
 */
export const theme = createTheme({
  primaryColor: "purple",
  primaryShade: 6,
  colors: {
    purple: purpleTuple,
    slate: slateTuple,
    // Mantine's default "gray" is what most components (inputs, borders,
    // subtle backgrounds) key off by default — pointing it at our slate
    // scale means unstyled Mantine primitives already match the palette
    // without every consumer having to opt in per-component.
    gray: slateTuple,
  },
  fontFamily,
  defaultRadius: "md",
  radius: {
    sm: radius.sm,
    md: radius.md,
    lg: radius.lg,
    xl: radius.xl,
  },
  headings: {
    fontFamily,
  },
  // KAN-443: a field's hint is an info icon beside the label and the
  // description is visually hidden; rendered AFTER the input it never moves
  // the input down (Mantine adds a top margin to an input that follows a
  // description), so hinted and plain fields in one row stay on one line.
  // Errors keep their place below the input.
  components: {
    TextInput: TextInput.extend({ defaultProps: { inputWrapperOrder: INPUT_WRAPPER_ORDER } }),
    PasswordInput: PasswordInput.extend({ defaultProps: { inputWrapperOrder: INPUT_WRAPPER_ORDER } }),
    NumberInput: NumberInput.extend({ defaultProps: { inputWrapperOrder: INPUT_WRAPPER_ORDER } }),
    Textarea: Textarea.extend({ defaultProps: { inputWrapperOrder: INPUT_WRAPPER_ORDER } }),
    Select: Select.extend({ defaultProps: { inputWrapperOrder: INPUT_WRAPPER_ORDER } }),
    MultiSelect: MultiSelect.extend({ defaultProps: { inputWrapperOrder: INPUT_WRAPPER_ORDER } }),
    TagsInput: TagsInput.extend({ defaultProps: { inputWrapperOrder: INPUT_WRAPPER_ORDER } }),
    Autocomplete: Autocomplete.extend({ defaultProps: { inputWrapperOrder: INPUT_WRAPPER_ORDER } }),
    FileInput: FileInput.extend({ defaultProps: { inputWrapperOrder: INPUT_WRAPPER_ORDER } }),
    CheckboxGroup: CheckboxGroup.extend({ defaultProps: { inputWrapperOrder: INPUT_WRAPPER_ORDER } }),
    RadioGroup: RadioGroup.extend({ defaultProps: { inputWrapperOrder: INPUT_WRAPPER_ORDER } }),
  },
});
