import type { ReactNode } from 'react'
import { AtSign, KeyRound, LogOut, MailPlus, ShieldOff, UserCog } from 'lucide-react'
import type { RowMenuGroups } from '@nexotec/ui-kit'
import type { RecoveryAction, RecoveryUserRead } from '../../api/types'

type T = (key: string, options?: Record<string, unknown>) => string

/** The four per-user actions, the Betrieb's grant-manager (FR-AU-25) and
 * the sign-in address (Anto, 2026-10-22). */
export type RecoveryChoice = RecoveryAction | 'grant-manager' | 'change-email'

/**
 * KAN-202 — the ONE definition of what platform staff can do to an account
 * (ADR-061): the recovery page's row menu renders from it. Every entry opens
 * the reason dialog; none acts on a click. Disabled entries say why, never
 * hide (§ The Data Grid — row menu) — the API refuses the same cases.
 */
export function buildRecoveryRowMenu(
  t: T,
  user: Pick<RecoveryUserRead, 'status' | 'isDealerManager' | 'lockedUntil' | 'failedLoginAttempts'>,
  onChoose: (choice: RecoveryChoice) => void,
  options: { isSelf: boolean },
): RowMenuGroups {
  const deactivated = user.status === 'deactivated'
  const blocked = options.isSelf ? t('recovery.rowMenu.selfDisabled') : deactivated ? t('recovery.rowMenu.deactivatedDisabled') : undefined
  const entry = (choice: RecoveryChoice, icon: ReactNode, extraReason?: string) => ({
    label: t(`recovery.actions.${choice}.menu`),
    icon,
    onClick: () => onChoose(choice),
    disabled: Boolean(blocked ?? extraReason),
    disabledReason: blocked ?? extraReason,
  })
  const notLocked = user.lockedUntil === null && user.failedLoginAttempts === 0 ? t('recovery.rowMenu.notLocked') : undefined
  const notManageable =
    user.isDealerManager
      ? t('recovery.rowMenu.alreadyManager')
      : user.status !== 'active'
        ? t('recovery.rowMenu.managerNeedsActive')
        : undefined
  return {
    edit: [
      entry('reissue-invitation', <MailPlus size={16} />),
      entry('clear-lockout', <KeyRound size={16} />, notLocked),
      entry('revoke-sessions', <LogOut size={16} />),
      entry('grant-manager', <UserCog size={16} />, notManageable),
      entry('change-email', <AtSign size={16} />),
    ],
    destructive: [entry('reset-mfa', <ShieldOff size={16} />)],
  }
}
