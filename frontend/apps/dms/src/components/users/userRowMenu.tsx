import { ArrowRightLeft, MailPlus, Pencil, PlayCircle, PauseCircle, UserX } from 'lucide-react'
import type { RowMenuGroups } from '@nexotec/ui-kit'
import type { UserRead } from '../../api/types'

type T = (key: string, options?: Record<string, unknown>) => string

export interface UserRowMenuHandlers {
  onEdit: () => void
  onResendInvitation: () => void
  onSuspend: () => void
  onReactivate: () => void
  onHandOver: () => void
  onDeactivate: () => void
}

/**
 * KAN-200 — the ONE definition of what can be done to a user (ADR-061):
 * the list's row menu renders from it. There is no delete, anywhere
 * (A-RULE-5): a leaver is deactivated, with the reassignment step.
 * Disabled entries say why, never hide (§ The Data Grid — row menu).
 *
 * The entries follow the lifecycle (Users → Lifecycle): an invited user is
 * resent their invitation (never activated by hand — accepting it is the
 * only way in); an active one can be suspended; a suspended or deactivated
 * one reactivated; a deactivated one's open records handed over (the retry
 * after a module failed during the deactivation, FR-A-37).
 */
export function buildUserRowMenu(
  t: T,
  user: Pick<UserRead, 'status'>,
  handlers: UserRowMenuHandlers,
  options: { isSelf: boolean },
): RowMenuGroups {
  const deactivated = user.status === 'deactivated'
  const invited = user.status === 'invited'
  const active = user.status === 'active'
  const lifecycle =
    user.status === 'suspended' || deactivated
      ? {
          label: t('usersAdmin.rowMenu.reactivate'),
          icon: <PlayCircle size={16} />,
          onClick: handlers.onReactivate,
        }
      : {
          label: t('usersAdmin.rowMenu.suspend'),
          icon: <PauseCircle size={16} />,
          onClick: handlers.onSuspend,
          disabled: options.isSelf || !active,
          disabledReason: options.isSelf
            ? t('usersAdmin.rowMenu.selfDisabled')
            : !active
              ? t('usersAdmin.rowMenu.suspendDisabledNotActive')
              : undefined,
        }
  const edit = [
    {
      label: t('usersAdmin.rowMenu.edit'),
      icon: <Pencil size={16} />,
      onClick: handlers.onEdit,
    },
    {
      label: t('usersAdmin.rowMenu.resendInvitation'),
      icon: <MailPlus size={16} />,
      onClick: handlers.onResendInvitation,
      disabled: !invited,
      disabledReason: !invited ? t('usersAdmin.rowMenu.resendInvitationDisabled') : undefined,
    },
    lifecycle,
    {
      label: t('usersAdmin.rowMenu.handOver'),
      icon: <ArrowRightLeft size={16} />,
      onClick: handlers.onHandOver,
      disabled: options.isSelf,
      disabledReason: options.isSelf ? t('usersAdmin.rowMenu.selfDisabled') : undefined,
    },
  ]
  return {
    edit,
    destructive: [
      {
        label: t('usersAdmin.rowMenu.deactivate'),
        icon: <UserX size={16} />,
        onClick: handlers.onDeactivate,
        disabled: deactivated || options.isSelf,
        disabledReason: deactivated
          ? t('usersAdmin.rowMenu.deactivateDisabledAlready')
          : options.isSelf
            ? t('usersAdmin.rowMenu.selfDisabled')
            : undefined,
      },
    ],
  }
}
