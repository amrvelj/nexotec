import { describe, expect, it } from 'vitest'
import { diffAgainstSeed } from './seedDiff'

describe('diffAgainstSeed (KAN-209)', () => {
  it('says when a text is the seed', () => {
    expect(diffAgainstSeed('Betrieb', 'Betrieb').same).toBe(true)
  })

  it('marks the changed words between the common start and end', () => {
    expect(diffAgainstSeed('Neuer Betrieb anlegen', 'Neue Garage anlegen')).toEqual({
      same: false,
      prefix: '',
      removed: 'Neuer Betrieb',
      added: 'Neue Garage',
      suffix: ' anlegen',
    })
    expect(diffAgainstSeed('{{count, number}} Einträge', '{{count, number}} Zeilen')).toMatchObject({
      prefix: '{{count, number}} ',
      removed: 'Einträge',
      added: 'Zeilen',
      suffix: '',
    })
  })

  it('handles a pure insertion and a pure deletion', () => {
    expect(diffAgainstSeed('Speichern', 'Speichern und schliessen')).toMatchObject({ removed: '', added: ' und schliessen' })
    expect(diffAgainstSeed('Speichern und schliessen', 'Speichern')).toMatchObject({ removed: ' und schliessen', added: '' })
  })
})
