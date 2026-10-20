import { Stack, Title } from '@mantine/core'
import { useTranslation } from 'react-i18next'
import { Outlet, useLocation, useNavigate } from 'react-router-dom'
import { DetailTabs } from '@nexotec/ui-kit'
import { useAuth } from '../../auth/AuthContext'

/** The tabs of My account, in order; each is its own route under /account. */
export const ACCOUNT_TABS = ['profile', 'security', 'connected-accounts', 'signature'] as const
export type AccountTab = (typeof ACCOUNT_TABS)[number]

const TAB_LABEL_KEY: Record<AccountTab, string> = {
  profile: 'account.tabs.profile',
  security: 'account.tabs.security',
  'connected-accounts': 'account.tabs.connectedAccounts',
  signature: 'account.tabs.signature',
}

/**
 * My account (KAN-201; Dealer Administration "My account" — every user, not
 * only the manager): Profile · Security · Connected accounts · Signature,
 * one route per tab so a link names the tab. A session that still owes a
 * second factor sees the Security tab alone — the API refuses the others
 * until a factor is enrolled (KAN-196).
 */
export function AccountLayout() {
  const { t } = useTranslation()
  const { mfaEnrolmentRequired } = useAuth()
  const location = useLocation()
  const navigate = useNavigate()
  const tabs = mfaEnrolmentRequired ? (['security'] as const) : ACCOUNT_TABS
  const active = ACCOUNT_TABS.find((tab) => location.pathname === `/account/${tab}`) ?? 'profile'

  return (
    <Stack gap="lg" p="md" maw={880}>
      <Title order={2}>{t('account.title')}</Title>
      <DetailTabs
        tabs={tabs.map((id) => ({ id, label: t(TAB_LABEL_KEY[id]) }))}
        activeTab={active}
        onTabChange={(id) => navigate(`/account/${id}`)}
      />
      <Outlet />
    </Stack>
  )
}
