import { describe, expect, it } from 'vitest'
import { providerText } from './providerText'

describe('providerText (KAN-595)', () => {
  const texts = { de: 'Wärmepumpe', fr: 'Pompe à chaleur', it: null, en: '' }

  it('reads the language asked for, regional tags included', () => {
    expect(providerText(texts, 'fr', 'Wärmepumpe')).toBe('Pompe à chaleur')
    expect(providerText(texts, 'fr-CH', 'Wärmepumpe')).toBe('Pompe à chaleur')
  })

  it('falls back to the delivered default where the provider sent no text', () => {
    expect(providerText(texts, 'it', 'Wärmepumpe')).toBe('Wärmepumpe')
    expect(providerText(texts, 'en', 'Wärmepumpe')).toBe('Wärmepumpe')
    expect(providerText(undefined, 'fr', 'Anhängerkupplung')).toBe('Anhängerkupplung')
  })
})
