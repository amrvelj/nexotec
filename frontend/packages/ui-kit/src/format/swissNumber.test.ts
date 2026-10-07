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

  // KAN-164: without fractionDigits Intl caps at 3 decimals; a DECIMAL(12, 4)
  // value needs all four kept, and trailing zeros shown at the column's scale.
  it("keeps exactly the fraction digits asked for", () => {
    expect(formatNumber(1234.5678)).toBe("1'234.568");
    expect(formatNumber(1234.5678, 4)).toBe("1'234.5678");
    expect(formatNumber(1234.5, 4)).toBe("1'234.5000");
    expect(formatNumber(0, 4)).toBe("0.0000");
    expect(formatNumber(12500, 0)).toBe("12'500");
  });
});

describe("formatCurrencyChf", () => {
  it("prints CHF, two decimals and a real minus sign", () => {
    expect(formatCurrencyChf(12500)).toBe("CHF 12'500.00");
    expect(formatCurrencyChf(23450.5)).toBe("CHF 23'450.50");
    expect(formatCurrencyChf(-1500)).toBe("− CHF 1'500.00");
  });
});
