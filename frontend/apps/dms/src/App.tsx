import { BrowserRouter, Navigate, Outlet, Route, Routes } from 'react-router-dom'
import { AuthProvider } from './auth/AuthContext'
import { ProtectedRoute } from './components/ProtectedRoute'
import { RequireEntitlement } from './components/RequireEntitlement'
import { DmsShell } from './layout/DmsShell'
import { LoginPage } from './pages/LoginPage'
import { lazyPage } from './layout/lazyPage'
import { RouteSuspense } from './layout/RouteSuspense'

// KAN-233: the sign-in screen, the session guard and the shell stay in the
// entry chunk, so signing in never waits on a chunk fetch; every other screen
// is fetched when its route is first opened.
const PasswordResetRequestPage = lazyPage(() => import('./pages/PasswordResetRequestPage'), 'PasswordResetRequestPage')
const PasswordResetConfirmPage = lazyPage(() => import('./pages/PasswordResetConfirmPage'), 'PasswordResetConfirmPage')
const InvitationAcceptPage = lazyPage(() => import('./pages/InvitationAcceptPage'), 'InvitationAcceptPage')
const AccountSecurityPage = lazyPage(() => import('./pages/AccountSecurityPage'), 'AccountSecurityPage')
const AccountConnectedAccountsPage = lazyPage(() => import('./pages/account/AccountConnectedAccountsPage'), 'AccountConnectedAccountsPage')
const AccountLayout = lazyPage(() => import('./pages/account/AccountLayout'), 'AccountLayout')
const AccountProfilePage = lazyPage(() => import('./pages/account/AccountProfilePage'), 'AccountProfilePage')
const AccountSignaturePage = lazyPage(() => import('./pages/account/AccountSignaturePage'), 'AccountSignaturePage')
const DashboardPage = lazyPage(() => import('./pages/DashboardPage'), 'DashboardPage')
const CustomersListPage = lazyPage(() => import('./pages/CustomersListPage'), 'CustomersListPage')
const CustomerCreatePage = lazyPage(() => import('./pages/CustomerCreatePage'), 'CustomerCreatePage')
const CustomerDetailPage = lazyPage(() => import('./pages/CustomerDetailPage'), 'CustomerDetailPage')
const CatalogueBrowsePage = lazyPage(() => import('./pages/CatalogueBrowsePage'), 'CatalogueBrowsePage')
const ReferenceDataPage = lazyPage(() => import('./pages/ReferenceDataPage'), 'ReferenceDataPage')
const AccountRecoveryPage = lazyPage(() => import('./pages/AccountRecoveryPage'), 'AccountRecoveryPage')
const UsersListPage = lazyPage(() => import('./pages/UsersListPage'), 'UsersListPage')
const IntegrationsPage = lazyPage(() => import('./pages/IntegrationsPage'), 'IntegrationsPage')
const VehiclesListPage = lazyPage(() => import('./pages/VehiclesListPage'), 'VehiclesListPage')
const VehicleDetailPage = lazyPage(() => import('./pages/VehicleDetailPage'), 'VehicleDetailPage')
const StockListPage = lazyPage(() => import('./pages/StockListPage'), 'StockListPage')
const StockCreatePage = lazyPage(() => import('./pages/StockCreatePage'), 'StockCreatePage')
const StockDetailPage = lazyPage(() => import('./pages/StockDetailPage'), 'StockDetailPage')
const SalesListPage = lazyPage(() => import('./pages/SalesListPage'), 'SalesListPage')
const OfferCreateRedirectPage = lazyPage(() => import('./pages/OfferDetailPage'), 'OfferCreateRedirectPage')
const OfferDetailPage = lazyPage(() => import('./pages/OfferDetailPage'), 'OfferDetailPage')
const ContractDetailPage = lazyPage(() => import('./pages/ContractDetailPage'), 'ContractDetailPage')
const ValuationsListPage = lazyPage(() => import('./pages/ValuationsListPage'), 'ValuationsListPage')
const ValuationCreatePage = lazyPage(() => import('./pages/ValuationCreatePage'), 'ValuationCreatePage')
const ValuationDetailPage = lazyPage(() => import('./pages/ValuationDetailPage'), 'ValuationDetailPage')

function ProtectedLayout() {
  return (
    <ProtectedRoute>
      <DmsShell>
        <RouteSuspense fill={false}>
          <Outlet />
        </RouteSuspense>
      </DmsShell>
    </ProtectedRoute>
  )
}

export default function App() {
  return (
    <BrowserRouter>
      <AuthProvider>
        {/* Screens outside the shell (the e-mail links' landing pages). Inside
            it, ProtectedLayout's own boundary keeps the shell on screen. */}
        <RouteSuspense fill>
          <Routes>
          <Route path="/login" element={<LoginPage />} />
          {/* KAN-196: the e-mail links (reset, invitation) land here, outside the shell. */}
          <Route path="/password-reset" element={<PasswordResetRequestPage />} />
          <Route path="/password-reset/confirm" element={<PasswordResetConfirmPage />} />
          <Route path="/invitations/accept" element={<InvitationAcceptPage />} />
          <Route element={<ProtectedLayout />}>
            <Route path="/" element={<DashboardPage />} />
            {/* KAN-201: My account — one route per tab. */}
            <Route path="/account" element={<AccountLayout />}>
              <Route index element={<Navigate to="/account/profile" replace />} />
              <Route path="profile" element={<AccountProfilePage />} />
              <Route path="security" element={<AccountSecurityPage />} />
              <Route path="connected-accounts" element={<AccountConnectedAccountsPage />} />
              <Route path="signature" element={<AccountSignaturePage />} />
            </Route>
            <Route path="/customers" element={<CustomersListPage />} />
            <Route path="/customers/new" element={<CustomerCreatePage />} />
            <Route path="/customers/:id" element={<CustomerDetailPage />} />
            <Route path="/catalogue" element={<CatalogueBrowsePage />} />
            {/* C-B (KAN-40): the mapping-gap queue moved into /catalogue as a tab.
                Kept as a redirect so existing deep links don't 404. */}
            <Route
              path="/vehicle-mdm/mapping-gaps"
              element={<Navigate to="/catalogue?tab=mapping-gaps" replace />}
            />
            <Route path="/settings/reference" element={<ReferenceDataPage />} />
            {/* KAN-200 — Dealer Administration › Users (manager-only at the API). */}
            <Route path="/administration/users" element={<UsersListPage />} />
            <Route path="/administration/account-recovery" element={<AccountRecoveryPage />} />
            <Route path="/integrations" element={<IntegrationsPage />} />
            <Route path="/vehicles" element={<VehiclesListPage />} />
            <Route path="/vehicles/:id" element={<VehicleDetailPage />} />
            {/* KAN-198 (D-A-04, D-A-10): stock, sales and valuations belong to the
                `sales` entitlement — a workshop without it never reaches them. */}
            <Route element={<RequireEntitlement module="sales" />}>
              <Route path="/stock" element={<StockListPage />} />
              <Route path="/stock/new" element={<StockCreatePage />} />
              <Route path="/stock/:id" element={<StockDetailPage />} />
              <Route path="/sales" element={<SalesListPage />} />
              <Route path="/sales/offers/new" element={<OfferCreateRedirectPage />} />
              <Route path="/sales/offers/:id" element={<OfferDetailPage />} />
              <Route path="/sales/contracts/:id" element={<ContractDetailPage />} />
              <Route path="/valuations" element={<ValuationsListPage />} />
              <Route path="/valuations/new" element={<ValuationCreatePage />} />
              <Route path="/valuations/:id" element={<ValuationDetailPage />} />
            </Route>
          </Route>
          <Route path="*" element={<Navigate to="/" replace />} />
          </Routes>
        </RouteSuspense>
      </AuthProvider>
    </BrowserRouter>
  )
}
