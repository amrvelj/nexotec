import { Stack, Text } from '@mantine/core'
import { useTranslation } from 'react-i18next'
import { GoogleDocumentsSentence, LegalDocument, ReviewPendingMarker, type LegalSection } from './legal/LegalDocument'

/** KAN-452: the public Privacy Policy (`/legal/privacy`), reachable without
 * a session. Its Places section carries the «Address suggestions» paragraph
 * of the runbook «Google Places go-live» (Google a separate controller; the
 * typed address text only; the per-Betrieb switch) and references Google's
 * Privacy Policy and Terms of Service, as Google's Places API policies
 * require. Every other section is a placeholder for Anto and counsel. */
export function PrivacyPolicyPage() {
  const { t } = useTranslation()
  const placeholder = (id: string): LegalSection => ({ id, title: t(`legal.privacy.sections.${id}`), kind: 'placeholder' })

  const sections: LegalSection[] = [
    placeholder('controller'),
    placeholder('data'),
    placeholder('purposes'),
    {
      id: 'places',
      title: t('legal.privacy.sections.places'),
      kind: 'content',
      body: (
        <Stack gap="xs">
          <ReviewPendingMarker />
          <Text fz="sm">{t('legal.privacy.places.body')}</Text>
          <Text fz="sm">
            <GoogleDocumentsSentence i18nKey="legal.privacy.places.google" />
          </Text>
        </Stack>
      ),
    },
    placeholder('recipients'),
    placeholder('retention'),
    placeholder('rights'),
  ]

  return <LegalDocument title={t('legal.privacy.title')} sections={sections} />
}
