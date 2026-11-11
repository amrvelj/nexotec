/** TanStack Query keys the Business screen's sections share (KAN-203). */
export const dealershipKey = (dealershipId: string) => ['dealership', dealershipId] as const
export const settingsKey = (dealershipId: string) => ['dealership', dealershipId, 'settings'] as const
export const bankAccountsKey = (dealershipId: string) => ['dealership', dealershipId, 'bank-accounts'] as const
export const authConfigKey = (dealershipId: string) => ['dealership', dealershipId, 'auth-config'] as const
