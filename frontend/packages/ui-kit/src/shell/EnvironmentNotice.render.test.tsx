// @vitest-environment jsdom
import { describe, expect, it, vi } from "vitest";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MantineProvider } from "@mantine/core";
import { testTheme } from "../test/testTheme";
import { EnvironmentNotice } from "./EnvironmentNotice";

// KAN-221 PR-6 — the one notice slot (ADR-084) also carries the support-access
// banner, whose one control ends the session. The strip stays without a dismiss.

describe("EnvironmentNotice", () => {
  it("renders the app's sentence and no control of its own", () => {
    render(
      <MantineProvider theme={testTheme}>
        <EnvironmentNotice>Dieser Server hat keinen Worker.</EnvironmentNotice>
      </MantineProvider>,
    );
    expect(screen.getByRole("status")).toHaveTextContent("Dieser Server hat keinen Worker.");
    expect(screen.queryByRole("button")).toBeNull();
  });

  it("renders the app's action at the strip's end", async () => {
    const onEnd = vi.fn();
    render(
      <MantineProvider theme={testTheme}>
        <EnvironmentNotice action={<button onClick={onEnd}>Sitzung beenden</button>}>Petra ist im Konto.</EnvironmentNotice>
      </MantineProvider>,
    );
    await userEvent.click(screen.getByRole("button", { name: "Sitzung beenden" }));
    expect(onEnd).toHaveBeenCalledOnce();
  });
});
