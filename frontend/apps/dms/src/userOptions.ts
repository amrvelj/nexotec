import type { AccessRole, EmploymentStatus, StandardTextCategory, UserRole, UserStatus } from './api/types'

type T = (key: string, options?: Record<string, unknown>) => string

/**
 * KAN-200 — the Users screen's vocabularies, translated at render time
 * (never a module-level constant: the labels follow the UI language).
 */

export const USER_ROLE_VALUES: UserRole[] = ['sales', 'service_advisor', 'finance_manager', 'gm', 'admin', 'technician', 'parts', 'other']

/** The functional roles a manager can grant (RP-3). `platform_admin` is
 * Nexotec staff only (KAN-97) and never offered here. */
export const GRANTABLE_ACCESS_ROLES: AccessRole[] = ['sales', 'aftersales', 'parts', 'inventory', 'finance', 'technician', 'auditor']

export const EMPLOYMENT_STATUS_VALUES: EmploymentStatus[] = ['active', 'on_leave', 'terminated']

export const USER_STATUS_VALUES: UserStatus[] = ['invited', 'active', 'suspended', 'deactivated']

export const TEXT_CATEGORY_VALUES: StandardTextCategory[] = ['document', 'email', 'sms', 'internal_note', 'workshop']

export function userRoleLabel(t: T, role: UserRole): string {
  return t(`userRole.${role}`)
}

export function accessRoleLabel(t: T, role: AccessRole): string {
  return t(`usersAdmin.accessRole.${role}`)
}

export function employmentStatusLabel(t: T, status: EmploymentStatus): string {
  return t(`usersAdmin.employmentStatus.${status}`)
}

export function textCategoryLabel(t: T, category: StandardTextCategory): string {
  return t(`usersAdmin.textCategory.${category}`)
}

/** The list's status cell: an invited user whose invitation ran out reads
 * "invitation expired", never "active" nor a bare "invited" (FR-A-11). */
export type DisplayUserStatus = UserStatus | 'invitation_expired'

export function displayUserStatus(status: UserStatus, invitationExpiresAt: string | null | undefined, now: Date = new Date()): DisplayUserStatus {
  if (status === 'invited' && invitationExpiresAt && new Date(invitationExpiresAt).getTime() < now.getTime()) return 'invitation_expired'
  return status
}

export function userStatusLabel(t: T, status: DisplayUserStatus): string {
  return t(`usersAdmin.status.${status}`)
}

export function userStatusTone(status: DisplayUserStatus): 'success' | 'warning' | 'slate' | 'destructive' | 'informational' {
  switch (status) {
    case 'active':
      return 'success'
    case 'invited':
      return 'informational'
    case 'invitation_expired':
      return 'warning'
    case 'suspended':
      return 'warning'
    case 'deactivated':
      return 'slate'
  }
}

/** The deactivation dialog's group labels (module + group, from the registry). */
// A group a module reports that has no label in the bundles renders the loud
// missing-key marker (ui.md): a new provider group ships with its four labels.
export function ownedGroupLabel(t: T, module: string, group: string): string {
  return t(`usersAdmin.ownedGroup.${module}.${group}`)
}

export function userDisplayName(user: { firstName: string; lastName: string }): string {
  return `${user.firstName} ${user.lastName}`.trim()
}
