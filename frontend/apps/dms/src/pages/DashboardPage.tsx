import { LayoutDashboard } from 'lucide-react'
import { useTranslation } from 'react-i18next'
import { slate, spacing, useSetBreadcrumb } from '@nexotec/ui-kit'

/**
 * WP-6c PR-2: the landing page's shell plumbing (the route itself,
 * breadcrumb root, sidebar entry above Master Data) has to exist for the
 * rest of the shell to be correct — the Dashboard's own content doesn't.
 * "The Dashboard needs its own PRD before it is built" (§ UI/UX
 * Specification), so this is deliberately an empty-state placeholder, not
 * a role-shaped stub of the real thing.
 */
export function DashboardPage() {
  const { t } = useTranslation()
  useSetBreadcrumb([t('shell.nav.dashboard')])

  return (
    <div
      style={{
        display: 'flex',
        flexDirection: 'column',
        alignItems: 'center',
        justifyContent: 'center',
        gap: spacing.md,
        padding: spacing.xl,
        minHeight: 320,
        textAlign: 'center',
        color: slate[5],
      }}
    >
      <LayoutDashboard size={24} strokeWidth={1.5} color={slate[4]} aria-hidden="true" />
      <div style={{ fontSize: 16, fontWeight: 600, color: slate[7] }}>{t('dashboard.placeholder.title')}</div>
      <div style={{ fontSize: 14, maxWidth: 420 }}>{t('dashboard.placeholder.body')}</div>
    </div>
  )
}
