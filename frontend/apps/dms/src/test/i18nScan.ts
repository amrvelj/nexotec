// i18n/index.ts renders `⚠ MISSING I18N KEY: <key>` for any key absent
// from every bundle (parseMissingKeyHandler). localeKeyParity.test.ts only
// compares the four bundles against each other, so a key missing from ALL
// four slips through — this is the scan a route walk uses to catch it in
// what a screen actually renders, attributes included.

const MARKER = '⚠ MISSING I18N KEY:'

export function collectMissingKeys(root: ParentNode = document.body): string[] {
  const found = new Set<string>()
  // The marker shows up both as text and inside attributes (aria-label,
  // placeholder, title), so scan the serialized HTML rather than
  // textContent. Keys are dotted identifiers; stop at the first quote,
  // angle bracket or whitespace that ends the interpolation.
  const html = (root as Element).innerHTML ?? ''
  const re = /⚠ MISSING I18N KEY:\s*([A-Za-z0-9_.:-]+)/g
  let m: RegExpExecArray | null
  while ((m = re.exec(html)) !== null) found.add(m[1])
  return [...found]
}

export function expectNoMissingKeys(context: string, root?: ParentNode): void {
  const missing = collectMissingKeys(root)
  if (missing.length > 0) {
    throw new Error(`${context}: rendered ${missing.length} missing i18n key(s): ${missing.join(', ')}`)
  }
}

export { MARKER }
