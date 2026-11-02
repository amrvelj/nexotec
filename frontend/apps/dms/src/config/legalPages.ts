// KAN-452: the two public pages Google's Places API policies require
// ("publicly accessible Terms of Use and a Privacy Policy that incorporate
// Google's Terms of Service and Privacy Policy"). Served by the SPA without a
// session; linked from every identity screen (AuthScreen) and from the
// shell's account menu. Paths only — no page module is imported from here,
// so the eager sign-in chunk stays free of them (KAN-233).

export const LEGAL_TERMS_PATH = '/legal/terms'
export const LEGAL_PRIVACY_PATH = '/legal/privacy'

/** Google's own documents, referenced in each page's Places section. */
export const GOOGLE_TERMS_URL = 'https://policies.google.com/terms'
export const GOOGLE_PRIVACY_URL = 'https://policies.google.com/privacy'
