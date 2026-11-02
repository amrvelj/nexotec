import { Stack, Text } from '@mantine/core'
import { useTranslation } from 'react-i18next'
import { GoogleDocumentsSentence, LegalDocument, ReviewPendingMarker, type LegalSection } from './legal/LegalDocument'

/** KAN-452: the public Terms of Use (`/legal/terms`), reachable without a
 * session. Google's Places API policies require the application's Terms of
 * Use to incorporate Google's Terms of Service — the Places section does.
 * Every other section is a placeholder for Anto and counsel. */
export function TermsOfUsePage() {
  const { t } = useTranslation()
  const placeholder = (id: string): LegalSection => ({ id, title: t(`legal.terms.sections.${id}`), kind: 'placeholder' })

  const sections: LegalSection[] = [
    placeholder('scope'),
    placeholder('services'),
    placeholder('access'),
    {
      id: 'places',
      title: t('legal.terms.sections.places'),
      kind: 'content',
      body: (
        <Stack gap="xs">
          <ReviewPendingMarker />
          <Text fz="sm">
            <GoogleDocumentsSentence i18nKey="legal.terms.places.body" />
          </Text>
        </Stack>
      ),
    },
    placeholder('liability'),
    placeholder('law'),
  ]

  return <LegalDocument title={t('legal.terms.title')} sections={sections} />
}
