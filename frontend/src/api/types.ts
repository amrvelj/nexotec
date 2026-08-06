// Mirrors app/schemas/{user,customer,auth}.py — camelCase JSON, as the
// backend's CamelModel convention produces.

export type AccessRole = 'platform_admin' | 'dealer_admin' | 'sales' | 'inventory' | 'auditor'
export type UserStatus = 'invited' | 'active' | 'suspended' | 'deactivated'

export interface UserRead {
  id: string
  dealerId: string
  firstName: string
  lastName: string
  email: string
  phone: string | null
  role: string
  accessRole: AccessRole
  employmentStatus: string
  authIdentityId: string
  status: UserStatus
  version: number
  createdAt: string
  updatedAt: string
}

export interface LoginResponse {
  user: UserRead
}

export type CustomerType = 'individual'
export type PreferredContactMethod = 'email' | 'phone' | 'sms'
export type CustomerLifecycleStatus = 'prospect' | 'active' | 'inactive' | 'merged' | 'do_not_contact'
export type CustomerSource = 'walk_in' | 'phone' | 'web_lead' | 'marketplace' | 'other'

export interface CustomerAddress {
  street: string
  houseNumber: string
  postalCode: string
  locality: string
  canton: string
  country: string
}

export interface CustomerRead {
  id: string
  tenantId: string
  customerType: CustomerType
  firstName: string
  lastName: string
  email: string | null
  phone: string | null
  address: CustomerAddress | null
  preferredContactMethod: PreferredContactMethod | null
  lifecycleStatus: CustomerLifecycleStatus
  source: CustomerSource | null
  sourceRef: string | null
  duplicateOfCustomerId: string | null
  marketingConsent: boolean
  version: number
  createdAt: string
  updatedAt: string
}

export interface CustomerPage {
  items: CustomerRead[]
  nextCursor: string | null
}

export interface CustomerCreateInput {
  firstName: string
  lastName: string
  email?: string | null
  phone?: string | null
  address?: CustomerAddress | null
  preferredContactMethod?: PreferredContactMethod | null
  lifecycleStatus?: CustomerLifecycleStatus
  source?: CustomerSource | null
  sourceRef?: string | null
}

export type CustomerUpdateInput = Partial<CustomerCreateInput>

export interface ApiErrorBody {
  error: {
    code: string
    message: string
    details: Record<string, unknown> | null
  }
}
