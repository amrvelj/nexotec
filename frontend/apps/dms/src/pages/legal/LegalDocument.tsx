import { useEffect, type ReactNode } from 'react'
import { Link, useSearchParams } from 'react-router-dom'
import { Alert, Anchor, Container, Group, Paper, SegmentedControl, Stack, Text, Title } from '@mantine/core'
import { Trans, useTranslation } from 'react-i18next'
import { SUPPORTED_LANGUAGES, type SupportedLanguage } from '../../i18n'
import { GOOGLE_PRIVACY_URL, GOOGLE_TERMS_URL, LEGAL_PRIVACY_PATH, LEGAL_TERMS_PATH } from '../../config/legalPages'

// KAN-452: the layout the two public legal pages share — Terms of Use and
// Privacy Policy, served without a session (Google's Places API policies
// require both to be publicly accessible). The content is Anto's and
// counsel's: the page says it is a draft, every section but the Places one is
// a marked placeholder, and the Places section is marked as awaiting review.

export type LegalSection =
  | { id: string; title: string; kind: 'placeholder' }
  | { id: string; title: string; kind: 'content'; body: ReactNode }

const isSupported = (value: string | null): value is SupportedLanguage =>
  (SUPPORTED_LANGUAGES as readonly string[]).includes(value ?? '')

/** A link to one of Google's documents inside a translated sentence. */
function GoogleLink({ href, children }: { href: string; children?: ReactNode }) {
  return (
    <Anchor href={href} target="_blank" rel="noopener noreferrer" inherit>
      {children}
    </Anchor>
  )
}

/** A translated sentence whose `<terms>` and `<privacy>` spans link
 * Google's Terms of Service and Privacy Policy. */
export function GoogleDocumentsSentence({ i18nKey }: { i18nKey: string }) {
  return (
    <Trans
      i18nKey={i18nKey}
      components={{ terms: <GoogleLink href={GOOGLE_TERMS_URL} />, privacy: <GoogleLink href={GOOGLE_PRIVACY_URL} /> }}
    />
  )
}

/** The Places section's own marker: its text is the runbook's proposal,
 * not yet reviewed by counsel. */
export function ReviewPendingMarker() {
  const { t } = useTranslation()
  return (
    <Text fz="xs" fw={600} c="dimmed" tt="uppercase">
      {t('legal.reviewPending')}
    </Text>
  )
}

export function LegalDocument({ title, sections }: { title: string; sections: LegalSection[] }) {
  const { t, i18n } = useTranslation()
  const [params, setParams] = useSearchParams()
  const requested = params.get('lang')

  // A public page has no user preference to read: `?lang=` makes a link in
  // one language shareable, and the switch below writes it. Nothing is
  // persisted — a signed-in user's UI language stays their preference.
  useEffect(() => {
    if (isSupported(requested) && i18n.resolvedLanguage !== requested) void i18n.changeLanguage(requested)
  }, [requested, i18n])

  const current = isSupported(i18n.resolvedLanguage ?? null) ? (i18n.resolvedLanguage as SupportedLanguage) : 'de'

  return (
    <Container size="md" py="xl" px="md">
      <Stack gap="lg">
        <Group justify="space-between" align="center">
          <Text fw={600} fz="lg">
            Nexotec
          </Text>
          <SegmentedControl
            size="xs"
            aria-label={t('legal.language')}
            value={current}
            onChange={(value) => {
              const next = new URLSearchParams(params)
              next.set('lang', value)
              setParams(next, { replace: true })
            }}
            data={SUPPORTED_LANGUAGES.map((lng) => ({ value: lng, label: lng.toUpperCase() }))}
          />
        </Group>
        <Paper withBorder radius="md" p="xl" shadow="xs">
          <Stack gap="lg">
            <Title order={1} fz="h2">
              {title}
            </Title>
            <Alert color="yellow" variant="light">
              {t('legal.draftNotice')}
            </Alert>
            {sections.map((section, index) => (
              <Stack key={section.id} gap="xs" component="section" aria-labelledby={`legal-${section.id}`}>
                <Title order={2} fz="h4" id={`legal-${section.id}`}>
                  {`${index + 1}. ${section.title}`}
                </Title>
                {section.kind === 'placeholder' ? (
                  <Text fz="sm" c="dimmed" fs="italic">
                    {t('legal.placeholder')}
                  </Text>
                ) : (
                  section.body
                )}
              </Stack>
            ))}
          </Stack>
        </Paper>
        <Group component="nav" aria-label={t('legal.linksLabel')} justify="center" gap="md">
          <Anchor component={Link} to="/login" fz="sm">
            {t('legal.toSignIn')}
          </Anchor>
          <Anchor component={Link} to={LEGAL_TERMS_PATH} fz="sm">
            {t('legal.termsLink')}
          </Anchor>
          <Anchor component={Link} to={LEGAL_PRIVACY_PATH} fz="sm">
            {t('legal.privacyLink')}
          </Anchor>
        </Group>
      </Stack>
    </Container>
  )
}
