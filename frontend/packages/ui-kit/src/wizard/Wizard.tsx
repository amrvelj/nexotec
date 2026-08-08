import type { ReactNode } from "react";
import { Check } from "lucide-react";
import { Button, Group } from "@mantine/core";
import { purple, radius, slate, spacing, typography } from "../tokens";
import type { WizardStep } from "./types";

export interface WizardProps {
  steps: WizardStep[];
  activeIndex: number;
  children: ReactNode;
  onBack: () => void;
  onNext: () => void;
  onCancel: () => void;
  nextLabel?: string;
  submitLabel?: string;
  nextDisabled?: boolean;
  submitting?: boolean;
  error?: ReactNode;
}

/**
 * § UI/UX Core Principles — "Multi-step forms show a numbered step header.
 * Steps are validated on advance, not at the end." This is the generic
 * step-shell: it owns none of the domain logic or per-step validation —
 * the caller drives activeIndex and decides what onNext does (advance vs.
 * submit), which is what makes the same shell usable for any multi-step
 * flow, not just customer creation.
 */
export function Wizard({
  steps,
  activeIndex,
  children,
  onBack,
  onNext,
  onCancel,
  nextLabel = "Next",
  submitLabel = "Submit",
  nextDisabled,
  submitting,
  error,
}: WizardProps) {
  const isLastStep = activeIndex === steps.length - 1;

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: spacing.xl }}>
      <div style={{ display: "flex", alignItems: "center" }}>
        {steps.map((step, index) => {
          const isCompleted = index < activeIndex;
          const isActive = index === activeIndex;
          const circleColor = isCompleted || isActive ? purple[6] : "transparent";
          const circleBorder = isCompleted || isActive ? purple[6] : slate[3];
          const textColor = isCompleted || isActive ? slate[9] : slate[4];

          return (
            <div key={step.id} style={{ display: "flex", alignItems: "center", flex: index < steps.length - 1 ? 1 : undefined }}>
              <div style={{ display: "flex", alignItems: "center", gap: spacing.sm, flexShrink: 0 }}>
                <div
                  aria-hidden="true"
                  style={{
                    width: 24,
                    height: 24,
                    borderRadius: radius.full,
                    border: `1.5px solid ${circleBorder}`,
                    backgroundColor: circleColor,
                    display: "flex",
                    alignItems: "center",
                    justifyContent: "center",
                    flexShrink: 0,
                    fontSize: 12,
                    fontWeight: 700,
                    color: isCompleted || isActive ? "#fff" : slate[4],
                  }}
                >
                  {isCompleted ? <Check size={13} /> : index + 1}
                </div>
                <span style={{ fontSize: typography.bodyStrong.size, fontWeight: typography.bodyStrong.weight, color: textColor, whiteSpace: "nowrap" }}>
                  {step.label}
                </span>
              </div>
              {index < steps.length - 1 && (
                <div
                  aria-hidden="true"
                  style={{ flex: 1, height: 1.5, backgroundColor: isCompleted ? purple[6] : slate[2], margin: `0 ${spacing.md}` }}
                />
              )}
            </div>
          );
        })}
      </div>

      <div>{children}</div>

      {error && (
        <div
          style={{
            padding: `${spacing.sm} ${spacing.md}`,
            borderRadius: radius.md,
            backgroundColor: "#FEF2F2",
            border: "1px solid #FECACA",
            color: "#DC2626",
            fontSize: 14,
          }}
        >
          {error}
        </div>
      )}

      <Group justify="space-between">
        <Button variant="subtle" color="gray" onClick={onCancel} type="button">
          Cancel
        </Button>
        <Group gap="sm">
          {activeIndex > 0 && (
            <Button variant="default" onClick={onBack} type="button" disabled={submitting}>
              Back
            </Button>
          )}
          <Button onClick={onNext} type="button" disabled={nextDisabled} loading={submitting}>
            {isLastStep ? submitLabel : nextLabel}
          </Button>
        </Group>
      </Group>
    </div>
  );
}
