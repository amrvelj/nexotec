import { createTheme, type MantineColorsTuple } from "@mantine/core";
import { fontFamily, purple, radius, slate } from "./tokens";

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
});
