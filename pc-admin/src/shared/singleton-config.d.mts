export interface NavigationItem { key: 'HOME' | 'CATEGORY' | 'CART' | 'ME'; label: string; iconAssetId: string | null; selectedIconAssetId: string | null }
export interface NavigationConfig { items: NavigationItem[] }
export interface CustomerServiceConfig { enabled: boolean; mode: 'PHONE' | 'QR' | null; phone: string | null; qrAssetId: string | null; prompt: string; iconAssetId: string | null }
export const NAV_ITEMS: ReadonlyArray<Readonly<{ key: NavigationItem['key']; label: string }>>
export function copyConfig<T>(config: T): T
export function reconcileSavedConfig<T>(current: T, submitted: T, server: T): { config: T; savedSnapshot: string; editedDuringSave: boolean }
export function updateNavigationItem(config: NavigationConfig, key: NavigationItem['key'], patch: Partial<NavigationItem>): NavigationConfig
export function validateNavigation(config: NavigationConfig): string
export function validateCustomerService(config: CustomerServiceConfig): string
