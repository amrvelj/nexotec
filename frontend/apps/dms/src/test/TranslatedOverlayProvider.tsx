import type { ReactNode } from 'react'
import { useTranslation } from 'react-i18next'
import { OverlayProvider } from '@nexotec/ui-kit'

/** The ADR-059 overlay stack as DmsShell mounts it, its close button named
 * in the UI language (KAN-364). */
export function TranslatedOverlayProvider({ children }: { children: ReactNode }) {
  const { t } = useTranslation()
  return <OverlayProvider closeLabel={t('common.overlay.close')}>{children}</OverlayProvider>
}
