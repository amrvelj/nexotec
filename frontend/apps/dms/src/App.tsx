import { BrowserRouter, Navigate, Outlet, Route, Routes } from 'react-router-dom'
import { AuthProvider } from './auth/AuthContext'
import { ProtectedRoute } from './components/ProtectedRoute'
import { DmsShell } from './layout/DmsShell'
import { LoginPage } from './pages/LoginPage'
import { CustomersListPage } from './pages/CustomersListPage'
import { CustomerFormPage } from './pages/CustomerFormPage'

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
          <Route element={<ProtectedLayout />}>
            <Route path="/customers" element={<CustomersListPage />} />
            <Route path="/customers/new" element={<CustomerFormPage />} />
            <Route path="/customers/:id" element={<CustomerFormPage />} />
          </Route>
          <Route path="*" element={<Navigate to="/customers" replace />} />
        </Routes>
      </AuthProvider>
    </BrowserRouter>
  )
}
