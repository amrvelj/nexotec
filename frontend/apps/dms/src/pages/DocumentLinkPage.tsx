import { useEffect, useState } from "react";
import { Alert, Button, Group, Loader, Stack, Text } from "@mantine/core";
import { useTranslation } from "react-i18next";
import { api } from "../api/client";
import { AuthScreen } from "./auth/AuthScreen";

type LinkState =
  | { kind: "loading" }
  | { kind: "ready"; url: string; filename: string | null }
  | { kind: "refused" };

/** KAN-217 PR-4 (ADR-193): the page a customer opens from an SMS or a
 * WhatsApp message — `/d#<token>`. No sign-in. The token is in the URL's
 * fragment, which a browser never sends to a server; the page takes it out of
 * the address bar at once and hands it to the API in a header. An unknown,
 * expired or used-up link is one message, as the server's one 404. */
export function DocumentLinkPage() {
  const { t } = useTranslation();
  // Read once, before any effect clears it (an effect may run twice).
  const [token] = useState(() => window.location.hash.replace(/^#/, ""));
  const [state, setState] = useState<LinkState>(
    token ? { kind: "loading" } : { kind: "refused" },
  );

  useEffect(() => {
    // The token leaves the address bar and the history entry at once.
    if (window.location.hash)
      window.history.replaceState(null, "", window.location.pathname);
    if (!token) return;
    let objectUrl: string | null = null;
    let cancelled = false;
    api
      .getDocumentLink(token)
      .then(({ url, filename }) => {
        objectUrl = url;
        if (cancelled) URL.revokeObjectURL(url);
        else setState({ kind: "ready", url, filename });
      })
      .catch(() => {
        if (!cancelled) setState({ kind: "refused" });
      });
    return () => {
      cancelled = true;
      if (objectUrl) URL.revokeObjectURL(objectUrl);
    };
  }, [token]);

  return (
    <AuthScreen title={t("documentLink.title")}>
      {state.kind === "loading" && (
        <Group gap="sm">
          <Loader size="sm" />
          <Text fz="sm">{t("documentLink.loading")}</Text>
        </Group>
      )}
      {state.kind === "refused" && (
        <Alert color="red" role="alert">
          {t("documentLink.refused")}
        </Alert>
      )}
      {state.kind === "ready" && (
        <Stack gap="md">
          <Text fz="sm">{t("documentLink.ready")}</Text>
          <Group grow>
            <Button
              component="a"
              href={state.url}
              target="_blank"
              rel="noopener noreferrer"
            >
              {t("documentLink.open")}
            </Button>
            <Button
              component="a"
              href={state.url}
              download={state.filename ?? true}
              variant="default"
            >
              {t("documentLink.download")}
            </Button>
          </Group>
        </Stack>
      )}
    </AuthScreen>
  );
}
