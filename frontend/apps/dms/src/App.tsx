import { BrowserRouter, Navigate, Outlet, Route, Routes } from 'react-router-dom'
import { AuthProvider } from './auth/AuthContext'
import { ProtectedRoute } from './components/ProtectedRoute'
import { RequireEntitlement } from './components/RequireEntitlement'
import { DmsShell } from './layout/DmsShell'
import { LoginPage } from './pages/LoginPage'
import { lazyPage } from './layout/lazyPage'
import { RouteSuspense } from './layout/RouteSuspense'
import { LEGAL_PRIVACY_PATH, LEGAL_TERMS_PATH } from './config/legalPages'

// KAN-233: the sign-in screen, the session guard and the shell stay in the
// entry chunk, so signing in never waits on a chunk fetch; every other screen
// is fetched when its route is first opened.
const PasswordResetRequestPage = lazyPage(() => import('./pages/PasswordResetRequestPage'), 'PasswordResetRequestPage')
const PasswordResetConfirmPage = lazyPage(() => import('./pages/PasswordResetConfirmPage'), 'PasswordResetConfirmPage')
const InvitationAcceptPage = lazyPage(() => import('./pages/InvitationAcceptPage'), 'InvitationAcceptPage')
const DocumentLinkPage = lazyPage(() => import('./pages/DocumentLinkPage'), 'DocumentLinkPage')
const TermsOfUsePage = lazyPage(() => import('./pages/TermsOfUsePage'), 'TermsOfUsePage')
const PrivacyPolicyPage = lazyPage(() => import('./pages/PrivacyPolicyPage'), 'PrivacyPolicyPage')
const AccountSecurityPage = lazyPage(() => import('./pages/AccountSecurityPage'), 'AccountSecurityPage')
const AccountConnectedAccountsPage = lazyPage(() => import('./pages/account/AccountConnectedAccountsPage'), 'AccountConnectedAccountsPage')
const AccountMailboxPage = lazyPage(() => import('./pages/account/AccountMailboxPage'), 'AccountMailboxPage')
const AccountLayout = lazyPage(() => import('./pages/account/AccountLayout'), 'AccountLayout')
const AccountProfilePage = lazyPage(() => import('./pages/account/AccountProfilePage'), 'AccountProfilePage')
const AccountSignaturePage = lazyPage(() => import('./pages/account/AccountSignaturePage'), 'AccountSignaturePage')
const AccountNotificationsPage = lazyPage(() => import('./pages/account/AccountNotificationsPage'), 'AccountNotificationsPage')
const NotificationsPage = lazyPage(() => import('./pages/NotificationsPage'), 'NotificationsPage')
const DashboardPage = lazyPage(() => import('./pages/DashboardPage'), 'DashboardPage')
const ContactsListPage = lazyPage(() => import('./modules/contact/pages/ContactsListPage'), 'ContactsListPage')
const ContactCreatePage = lazyPage(() => import('./modules/contact/pages/ContactCreatePage'), 'ContactCreatePage')
const ContactDetailPage = lazyPage(() => import('./modules/contact/pages/ContactDetailPage'), 'ContactDetailPage')
const CatalogueBrowsePage = lazyPage(() => import('./modules/vehicle/pages/CatalogueBrowsePage'), 'CatalogueBrowsePage')
const ReferenceDataPage = lazyPage(() => import('./pages/ReferenceDataPage'), 'ReferenceDataPage')
const AccountRecoveryPage = lazyPage(() => import('./pages/AccountRecoveryPage'), 'AccountRecoveryPage')
const TranslationsPage = lazyPage(() => import('./pages/TranslationsPage'), 'TranslationsPage')
const OnboardingPage = lazyPage(() => import('./pages/OnboardingPage'), 'OnboardingPage')
const UsersListPage = lazyPage(() => import('./pages/UsersListPage'), 'UsersListPage')
const BusinessPage = lazyPage(() => import('./pages/BusinessPage'), 'BusinessPage')
const LocationsListPage = lazyPage(() => import('./pages/LocationsListPage'), 'LocationsListPage')
const LocationDetailPage = lazyPage(() => import('./pages/LocationDetailPage'), 'LocationDetailPage')
const BrandingPage = lazyPage(() => import('./pages/BrandingPage'), 'BrandingPage')
const PublishingPage = lazyPage(() => import('./pages/PublishingPage'), 'PublishingPage')
const LetterheadPage = lazyPage(() => import('./pages/LetterheadPage'), 'LetterheadPage')
const StandardTextsPage = lazyPage(() => import('./pages/StandardTextsPage'), 'StandardTextsPage')
const AiAdministrationPage = lazyPage(() => import('./pages/AiAdministrationPage'), 'AiAdministrationPage')
const MailAdministrationPage = lazyPage(() => import('./pages/MailAdministrationPage'), 'MailAdministrationPage')
const MailLinkCallbackPage = lazyPage(() => import('./pages/MailLinkCallbackPage'), 'MailLinkCallbackPage')
const BusinessProfileLinkCallbackPage = lazyPage(
  () => import('./pages/BusinessProfileLinkCallbackPage'),
  'BusinessProfileLinkCallbackPage'
)
const NumberRangesPage = lazyPage(() => import('./pages/NumberRangesPage'), 'NumberRangesPage')
const LabourRatesPage = lazyPage(() => import('./pages/LabourRatesPage'), 'LabourRatesPage')
const PlannerResourcesPage = lazyPage(() => import('./pages/PlannerResourcesPage'), 'PlannerResourcesPage')
const PlannerDayPage = lazyPage(() => import('./modules/planning/pages/PlannerDayPage'), 'PlannerDayPage')
const SetupChecklistPage = lazyPage(() => import('./pages/SetupChecklistPage'), 'SetupChecklistPage')
const LegalAcceptancePage = lazyPage(() => import('./pages/LegalAcceptancePage'), 'LegalAcceptancePage')
const SetupInProgressPage = lazyPage(() => import('./pages/SetupInProgressPage'), 'SetupInProgressPage')
const PlatformLegalDocumentsPage = lazyPage(() => import('./pages/PlatformLegalDocumentsPage'), 'PlatformLegalDocumentsPage')
const StatusPage = lazyPage(() => import('./pages/StatusPage'), 'StatusPage')
const PlatformStatusPage = lazyPage(() => import('./pages/PlatformStatusPage'), 'PlatformStatusPage')
const SupportSessionLogPage = lazyPage(() => import('./pages/SupportSessionLogPage'), 'SupportSessionLogPage')
const PlatformUsersPage = lazyPage(() => import('./pages/PlatformUsersPage'), 'PlatformUsersPage')
const ContactGroupsPage = lazyPage(() => import('./pages/ContactGroupsPage'), 'ContactGroupsPage')
const SettingsIndexPage = lazyPage(() => import('./pages/SettingsIndexPage'), 'SettingsIndexPage')
const SettingsAreaPage = lazyPage(() => import('./pages/SettingsAreaPage'), 'SettingsAreaPage')
const DataExportPage = lazyPage(() => import('./pages/DataExportPage'), 'DataExportPage')
const PlatformOffboardingPage = lazyPage(() => import('./pages/PlatformOffboardingPage'), 'PlatformOffboardingPage')
const AdministrationIntegrationsPage = lazyPage(() => import('./pages/IntegrationsPage'), 'AdministrationIntegrationsPage')
const PlatformProvidersPage = lazyPage(() => import('./pages/IntegrationsPage'), 'PlatformProvidersPage')
const IntegrationsRedirect = lazyPage(() => import('./pages/IntegrationsPage'), 'IntegrationsRedirect')
const VehiclesListPage = lazyPage(() => import('./modules/vehicle/pages/VehiclesListPage'), 'VehiclesListPage')
const VehicleDetailPage = lazyPage(() => import('./modules/vehicle/pages/VehicleDetailPage'), 'VehicleDetailPage')
const StockListPage = lazyPage(() => import('./modules/stock/pages/StockListPage'), 'StockListPage')
const StockDetailPage = lazyPage(() => import('./modules/stock/pages/StockDetailPage'), 'StockDetailPage')
const SalesListPage = lazyPage(() => import('./modules/sales/pages/SalesListPage'), 'SalesListPage')
const OfferCreateRedirectPage = lazyPage(() => import('./modules/sales/pages/OfferDetailPage'), 'OfferCreateRedirectPage')
const OfferDetailPage = lazyPage(() => import('./modules/sales/pages/OfferDetailPage'), 'OfferDetailPage')
const ContractDetailPage = lazyPage(() => import('./modules/sales/pages/ContractDetailPage'), 'ContractDetailPage')
const ValuationsListPage = lazyPage(() => import('./modules/valuation/pages/ValuationsListPage'), 'ValuationsListPage')
const ValuationCreatePage = lazyPage(() => import('./modules/valuation/pages/ValuationCreatePage'), 'ValuationCreatePage')
const ValuationDetailPage = lazyPage(() => import('./modules/valuation/pages/ValuationDetailPage'), 'ValuationDetailPage')

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
            {/* KAN-217 PR-4: a contact's secure document link, `/d?lang=…#<token>` — no session. */}
            <Route path="/d" element={<DocumentLinkPage />} />
            {/* KAN-452: the public Terms of Use and Privacy Policy Google's Places
                API policies require — no session, outside the shell, for everyone. */}
            <Route path={LEGAL_TERMS_PATH} element={<TermsOfUsePage />} />
            <Route path={LEGAL_PRIVACY_PATH} element={<PrivacyPolicyPage />} />
            <Route element={<ProtectedLayout />}>
              <Route path="/" element={<DashboardPage />} />
              {/* KAN-201: My account — one route per tab. */}
              <Route path="/account" element={<AccountLayout />}>
                <Route index element={<Navigate to="/account/profile" replace />} />
                <Route path="profile" element={<AccountProfilePage />} />
                <Route path="security" element={<AccountSecurityPage />} />
                <Route path="connected-accounts" element={<AccountConnectedAccountsPage />} />
                {/* KAN-215: the user's own mailbox for contact mail. */}
                <Route path="mailbox" element={<AccountMailboxPage />} />
                <Route path="signature" element={<AccountSignaturePage />} />
                {/* KAN-216: the user's own notification preferences and this device's push. */}
                <Route path="notifications" element={<AccountNotificationsPage />} />
              </Route>
              {/* KAN-216 (FR-A-50): the inbox — the bell in the sidebar opens it. */}
              <Route path="/notifications" element={<NotificationsPage />} />
              <Route path="/contacts" element={<ContactsListPage />} />
              <Route path="/contacts/new" element={<ContactCreatePage />} />
              <Route path="/contacts/:id" element={<ContactDetailPage />} />
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
              {/* KAN-203 — Dealer Administration › Business: the Betrieb's master data. */}
              <Route path="/administration/business" element={<BusinessPage />} />
              <Route path="/administration/locations" element={<LocationsListPage />} />
              <Route path="/administration/locations/:locationId" element={<LocationDetailPage />} />
              <Route path="/administration/branding" element={<BrandingPage />} />
              <Route path="/administration/publishing" element={<PublishingPage />} />
              <Route path="/administration/letterhead" element={<LetterheadPage />} />
              {/* KAN-208 — Dealer Administration › Standard texts (any role reads; writes per category). */}
              <Route path="/administration/texts" element={<StandardTextsPage />} />
              {/* KAN-227 — Dealer Administration › AI texts: the AI connection in use and the prompts. */}
              <Route path="/administration/ai" element={<AiAdministrationPage />} />
              {/* KAN-215: the Betrieb's shared mailbox, and where the provider sends the browser back. */}
              <Route path="/administration/mail" element={<MailAdministrationPage />} />
              <Route path="/mail-link/callback" element={<MailLinkCallbackPage />} />
              <Route path="/business-profile-link/callback" element={<BusinessProfileLinkCallbackPage />} />
              <Route path="/administration/number-ranges" element={<NumberRangesPage />} />
              {/* KAN-806: the labour-rates screen embeds the settings rows of O16, O18 and O19; the area
                  itself is /administration/settings/labour_rates (KAN-805 criterion 6). */}
              <Route path="/administration/labour-rates" element={<LabourRatesPage />} />
              {/* KAN-808: the planner's resources and lists, embedding the settings rows of O6 and O7;
                  the area itself is /administration/settings/planning (KAN-805 criterion 6). */}
              <Route path="/administration/planner-resources" element={<PlannerResourcesPage />} />
              {/* KAN-214 — the setup checklist and «Betrieb aktivieren» (D-A-21, FR-A-34),
                  the re-acceptance of a new ToS / DPA version (FR-A-39), and what a
                  colleague sees while the Betrieb is being set up. */}
              <Route path="/administration/setup" element={<SetupChecklistPage />} />
              <Route path="/administration/legal-acceptance" element={<LegalAcceptancePage />} />
              <Route path="/setup-in-progress" element={<SetupInProgressPage />} />
              <Route path="/administration/contact-groups" element={<ContactGroupsPage />} />
              <Route path="/administration/settings" element={<SettingsIndexPage />} />
              <Route path="/administration/settings/:area" element={<SettingsAreaPage />} />
              <Route path="/administration/export" element={<DataExportPage />} />
              <Route path="/administration/account-recovery" element={<AccountRecoveryPage />} />
              {/* KAN-209 — Platform Administration › Translations (D-A-23). */}
              <Route path="/platform/translations" element={<TranslationsPage />} />
              {/* KAN-210 — Platform Administration › Onboarding (D-A-06, FR-A-24/25). */}
              <Route path="/platform/onboarding" element={<OnboardingPage />} />
              <Route path="/platform/offboarding" element={<PlatformOffboardingPage />} />
              {/* KAN-214 — Platform Administration › Legal documents (FR-A-39). */}
              <Route path="/platform/legal-documents" element={<PlatformLegalDocumentsPage />} />
              {/* KAN-223 — the Betrieb's status page (D-A-38, every role reads) and
                  Platform Administration › Nexotec status (staff write the incidents). */}
              <Route path="/administration/status" element={<StatusPage />} />
              <Route path="/platform/status" element={<PlatformStatusPage />} />
              {/* KAN-221 PR-6 — Dealer Administration › Support sessions (the manager's
                  log, FR-A-15) and Platform Administration › Platform users (ADR-188). */}
              <Route path="/administration/support-sessions" element={<SupportSessionLogPage />} />
              <Route path="/platform/users" element={<PlatformUsersPage />} />
              {/* KAN-222: the dealer's integrations are a section of Dealer Administration
                  (A-RULE-0), the platform's view is /platform/providers; the old path redirects. */}
              <Route path="/administration/integrations" element={<AdministrationIntegrationsPage />} />
              <Route path="/platform/providers" element={<PlatformProvidersPage />} />
              <Route path="/integrations" element={<IntegrationsRedirect />} />
              <Route path="/vehicles" element={<VehiclesListPage />} />
              <Route path="/vehicles/:id" element={<VehicleDetailPage />} />
              {/* KAN-198 (D-A-04, D-A-10): stock, sales and valuations belong to the
                  `sales` entitlement — a workshop without it never reaches them. */}
              <Route element={<RequireEntitlement module="sales" />}>
                <Route path="/stock" element={<StockListPage />} />
                <Route path="/stock/:id" element={<StockDetailPage />} />
                <Route path="/sales" element={<SalesListPage />} />
                <Route path="/sales/offers/new" element={<OfferCreateRedirectPage />} />
                <Route path="/sales/offers/:id" element={<OfferDetailPage />} />
                <Route path="/sales/contracts/:id" element={<ContractDetailPage />} />
                <Route path="/valuations" element={<ValuationsListPage />} />
                <Route path="/planner" element={<PlannerDayPage />} />
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
