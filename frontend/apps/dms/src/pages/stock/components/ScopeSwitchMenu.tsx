import { Button, Menu } from '@mantine/core'
import { Building2, ChevronDown, Warehouse } from 'lucide-react'
import { useTranslation } from 'react-i18next'

export type StockScope = 'own' | 'group'

interface ScopeSwitchMenuProps {
  scope: StockScope
  onScopeChange: (scope: StockScope) => void
}

/**
 * § ADR-055 — a scope SWITCH on the same overview screen, never a
 * separate nav entry or a second screen. Matches the live reference
 * prototype's own "Eigener Bestand" topbar control exactly: one button,
 * labelled with the current scope.
 */
export function ScopeSwitchMenu({ scope, onScopeChange }: ScopeSwitchMenuProps) {
  const { t } = useTranslation()

  return (
    <Menu position="bottom-end" shadow="md">
      <Menu.Target>
        <Button
          variant="default"
          leftSection={scope === 'own' ? <Warehouse size={16} /> : <Building2 size={16} />}
          rightSection={<ChevronDown size={14} />}
        >
          {scope === 'own' ? t('stockList.scope.own') : t('stockList.scope.group')}
        </Button>
      </Menu.Target>
      <Menu.Dropdown>
        <Menu.Item leftSection={<Warehouse size={16} />} onClick={() => onScopeChange('own')}>
          {t('stockList.scope.own')}
        </Menu.Item>
        <Menu.Item leftSection={<Building2 size={16} />} onClick={() => onScopeChange('group')}>
          {t('stockList.scope.group')}
        </Menu.Item>
      </Menu.Dropdown>
    </Menu>
  )
}
