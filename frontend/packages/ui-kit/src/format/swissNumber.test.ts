import { describe, expect, it } from "vitest";
import { formatCurrencyChf, formatNumber } from "./swissNumber";

// KAN-160: the kit holds the one implementation, so the kit's own suite pins
// it — apps/dms's format.test.ts covers the same functions through the
// re-export, including the KAN-156 newer-CLDR emulation.

describe("formatNumber", () => {
  it("groups with the ASCII apostrophe and uses a period decimal", () => {
    expect(formatNumber(12500)).toBe("12'500");
    expect(formatNumber(1250000)).toBe("1'250'000");
    expect(formatNumber(1234.5)).toBe("1'234.5");
    expect(formatNumber(999)).toBe("999");
  });
});

describe("formatCurrencyChf", () => {
  it("prints CHF, two decimals and a real minus sign", () => {
    expect(formatCurrencyChf(12500)).toBe("CHF 12'500.00");
    expect(formatCurrencyChf(23450.5)).toBe("CHF 23'450.50");
    expect(formatCurrencyChf(-1500)).toBe("− CHF 1'500.00");
  });
});
