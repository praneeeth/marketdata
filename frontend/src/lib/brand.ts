/** Product identity. Keep in sync with src/platform/branding.py. */
export const PRODUCT_NAME = 'Candlewise'
export const TAGLINE = 'Read the market. Decide for yourself.'
export const REPO_URL = 'https://github.com/praneeeth/marketdata'
export const RELEASES_URL = `${REPO_URL}/releases`
export const UPSTREAM_NAME = 'PanWatch'
export const UPSTREAM_URL = 'https://github.com/TNT-Likely/PanWatch'

const LEGACY_PREFIX = 'panwatch'
const PREFIX = 'candlewise'

function migrateStore(store: Storage): void {
  const legacyKeys: string[] = []
  for (let i = 0; i < store.length; i++) {
    const key = store.key(i)
    if (key && key.startsWith(LEGACY_PREFIX)) legacyKeys.push(key)
  }
  for (const key of legacyKeys) {
    const next = PREFIX + key.slice(LEGACY_PREFIX.length)
    const value = store.getItem(key)
    if (value !== null && store.getItem(next) === null) store.setItem(next, value)
    store.removeItem(key)
  }
}

/**
 * Carry browser-stored preferences over from the PanWatch name (theme, filters,
 * onboarding state, running-task ids): `panwatch*` keys become `candlewise*`.
 * A value already stored under the new key wins. Run once, before the app renders.
 */
export function migrateLegacyStorage(): void {
  for (const getStore of [() => window.localStorage, () => window.sessionStorage]) {
    try {
      migrateStore(getStore())
    } catch {
      // storage unavailable (private mode, blocked site data): nothing to migrate
    }
  }
}
