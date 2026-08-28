import { BrowserRouter, Navigate, Outlet, Route, Routes } from 'react-router-dom'
import { AuthProvider } from './auth/AuthContext'
import { ProtectedRoute } from './components/ProtectedRoute'
import { DmsShell } from './layout/DmsShell'
import { LoginPage } from './pages/LoginPage'
import { SignInErrorPage } from './pages/SignInErrorPage'
import { CustomersListPage } from './pages/CustomersListPage'
import { CustomerCreatePage } from './pages/CustomerCreatePage'
import { CustomerDetailPage } from './pages/CustomerDetailPage'
import { MappingGapsPage } from './pages/MappingGapsPage'

function ProtectedLayout() {
  return (
    <ProtectedRoute>
      <DmsShell>
        <Outlet />
      </DmsShell>
    </ProtectedRoute>
  )
}

export default function App() {
  return (
    <BrowserRouter>
      <AuthProvider>
        <Routes>
          <Route path="/login" element={<LoginPage />} />
          <Route path="/sign-in-error" element={<SignInErrorPage />} />
          <Route element={<ProtectedLayout />}>
            <Route path="/customers" element={<CustomersListPage />} />
            <Route path="/customers/new" element={<CustomerCreatePage />} />
            <Route path="/customers/:id" element={<CustomerDetailPage />} />
            <Route path="/vehicle-mdm/mapping-gaps" element={<MappingGapsPage />} />
          </Route>
          <Route path="*" element={<Navigate to="/customers" replace />} />
        </Routes>
      </AuthProvider>
    </BrowserRouter>
  )
}
