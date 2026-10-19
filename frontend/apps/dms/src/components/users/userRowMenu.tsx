import { MailPlus, Pencil, PlayCircle, PauseCircle, UserX } from 'lucide-react'
import type { RowMenuGroups } from '@nexotec/ui-kit'
import type { UserRead } from '../../api/types'

type T = (key: string, options?: Record<string, unknown>) => string

export interface UserRowMenuHandlers {
  onEdit: () => void
  onResendInvitation: () => void
  onSuspend: () => void
  onReactivate: () => void
  onDeactivate: () => void
}

/**
 * KAN-200 — the ONE definition of what can be done to a user (ADR-061):
 * the list's row menu renders from it. There is no delete, anywhere
 * (A-RULE-5): a leaver is deactivated, with the reassignment step.
 * Disabled entries say why, never hide (§ The Data Grid — row menu).
 */
export function buildUserRowMenu(
  t: T,
  user: Pick<UserRead, 'status'>,
  handlers: UserRowMenuHandlers,
  options: { isSelf: boolean },
): RowMenuGroups {
  const deactivated = user.status === 'deactivated'
  const invited = user.status === 'invited'
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
    user.status === 'suspended'
      ? {
          label: t('usersAdmin.rowMenu.reactivate'),
          icon: <PlayCircle size={16} />,
          onClick: handlers.onReactivate,
        }
      : deactivated
        ? {
            label: t('usersAdmin.rowMenu.reactivate'),
            icon: <PlayCircle size={16} />,
            onClick: handlers.onReactivate,
          }
        : {
            label: t('usersAdmin.rowMenu.suspend'),
            icon: <PauseCircle size={16} />,
            onClick: handlers.onSuspend,
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
