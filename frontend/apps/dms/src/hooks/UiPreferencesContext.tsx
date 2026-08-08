import { createContext, useContext, type ReactNode } from 'react'
import { useUiPreferences } from './useUiPreferences'

type UiPreferencesValue = ReturnType<typeof useUiPreferences>

const UiPreferencesContext = createContext<UiPreferencesValue | null>(null)

/** Calls useUiPreferences exactly once and shares it — DmsShell needs
 * sidebarCollapsed/uiLanguage, individual pages need density, and neither
 * should run its own independent fetch/local-mirror copy of the same
 * `ui` preference scope. */
export function UiPreferencesProvider({ children }: { children: ReactNode }) {
  const value = useUiPreferences()
  return <UiPreferencesContext.Provider value={value}>{children}</UiPreferencesContext.Provider>
}

export function useUiPreferencesContext(): UiPreferencesValue {
  const ctx = useContext(UiPreferencesContext)
  if (!ctx) throw new Error('useUiPreferencesContext must be used within a UiPreferencesProvider')
  return ctx
}
