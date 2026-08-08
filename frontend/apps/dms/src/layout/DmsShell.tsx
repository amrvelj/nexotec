import type { ReactNode } from 'react'
import { Link, useLocation, useNavigate } from 'react-router-dom'
import {
  ArrowLeftRight,
  Car,
  ChartColumn,
  Cog,
  Handshake,
  Receipt,
  ShieldCheck,
  Store,
  Users,
  Warehouse,
  Wrench,
} from 'lucide-react'
import { AppShell, purple, type NavGroupConfig } from '@nexotec/ui-kit'
import { useAuth } from '../auth/AuthContext'
import { useUiPreferences } from '../hooks/useUiPreferences'

const NAV_GROUPS: NavGroupConfig[] = [
  {
    label: 'Master Data',
    items: [
      { label: 'Customers', href: '/customers', icon: Users, status: 'active' },
      { label: 'Vehicles', href: '/vehicles', icon: Car, status: 'soon' },
      { label: 'Partners', href: '/partners', icon: Store, status: 'soon' },
    ],
  },
  {
    label: 'Modules',
    items: [
      { label: 'Sales', href: '/sales', icon: Handshake, status: 'soon' },
      { label: 'Aftersales', href: '/aftersales', icon: Wrench, status: 'soon' },
      { label: 'Inventory', href: '/inventory', icon: Warehouse, status: 'soon' },
      { label: 'Parts', href: '/parts', icon: Cog, status: 'soon' },
      { label: 'Finance', href: '/finance', icon: Receipt, status: 'soon' },
      { label: 'Transactions', href: '/transactions', icon: ArrowLeftRight, status: 'soon' },
      { label: 'Reporting', href: '/reporting', icon: ChartColumn, status: 'soon' },
      { label: 'Compliance', href: '/compliance', icon: ShieldCheck, status: 'soon' },
    ],
  },
]

function BrandMark() {
  return (
    <div
      aria-hidden="true"
      style={{
        width: 28,
        height: 28,
        borderRadius: 8,
        backgroundColor: purple[6],
        color: '#fff',
        display: 'flex',
        alignItems: 'center',
        justifyContent: 'center',
        fontSize: 14,
        fontWeight: 700,
        flexShrink: 0,
      }}
    >
      N
    </div>
  )
}

/** The application shell wired up for the DMS app specifically — nav
 * structure, auth, and preference persistence live here; the shell
 * components themselves (@nexotec/ui-kit) know nothing about any of it.
 */
export function DmsShell({ children }: { children: ReactNode }) {
  const { user, logout } = useAuth()
  const navigate = useNavigate()
  const location = useLocation()
  const { sidebarCollapsed, uiLanguage, setSidebarCollapsed, setUiLanguage } = useUiPreferences()

  const activeHref = '/' + (location.pathname.split('/')[1] ?? '')

  if (!user) return <>{children}</>

  return (
    <AppShell
      collapsed={sidebarCollapsed}
      onToggleCollapsed={() => setSidebarCollapsed(!sidebarCollapsed)}
      sidebar={{
        brand: <BrandMark />,
        productName: 'Nexotec',
        moduleSubtitle: 'DMS',
        groups: NAV_GROUPS,
        activeHref,
        user: { name: `${user.firstName} ${user.lastName}`, role: user.role },
        linkComponent: Link,
      }}
      topbar={{
        user: { name: `${user.firstName} ${user.lastName}`, email: user.email },
        uiLanguage,
        onLanguageChange: setUiLanguage,
        onSignOut: () => {
          logout().then(() => navigate('/login'))
        },
      }}
    >
      {children}
    </AppShell>
  )
}
