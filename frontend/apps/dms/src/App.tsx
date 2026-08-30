import { BrowserRouter, Navigate, Outlet, Route, Routes } from 'react-router-dom'
import { AuthProvider } from './auth/AuthContext'
import { ProtectedRoute } from './components/ProtectedRoute'
import { DmsShell } from './layout/DmsShell'
import { LoginPage } from './pages/LoginPage'
import { SignInErrorPage } from './pages/SignInErrorPage'
import { DashboardPage } from './pages/DashboardPage'
import { CustomersListPage } from './pages/CustomersListPage'
import { CustomerCreatePage } from './pages/CustomerCreatePage'
import { CustomerDetailPage } from './pages/CustomerDetailPage'
import { MappingGapsPage } from './pages/MappingGapsPage'
import { VehiclesListPage } from './pages/VehiclesListPage'
import { VehicleDetailPage } from './pages/VehicleDetailPage'
import { StockListPage } from './pages/StockListPage'
import { StockCreatePage } from './pages/StockCreatePage'
import { StockDetailPage } from './pages/StockDetailPage'

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
            <Route path="/" element={<DashboardPage />} />
            <Route path="/customers" element={<CustomersListPage />} />
            <Route path="/customers/new" element={<CustomerCreatePage />} />
            <Route path="/customers/:id" element={<CustomerDetailPage />} />
            <Route path="/vehicle-mdm/mapping-gaps" element={<MappingGapsPage />} />
            <Route path="/vehicles" element={<VehiclesListPage />} />
            <Route path="/vehicles/:id" element={<VehicleDetailPage />} />
            <Route path="/stock" element={<StockListPage />} />
            <Route path="/stock/new" element={<StockCreatePage />} />
            <Route path="/stock/:id" element={<StockDetailPage />} />
          </Route>
          <Route path="*" element={<Navigate to="/" replace />} />
        </Routes>
      </AuthProvider>
    </BrowserRouter>
  )
}
