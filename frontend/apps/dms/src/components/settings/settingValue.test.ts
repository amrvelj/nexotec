import { describe, expect, it } from 'vitest'
import type { SettingDeclarationRead } from '../../api/types'
import { fromDraft, toDraft } from './settingValue'

const declaration = (kind: string): SettingDeclarationRead => ({
  key: 'testarea.x',
  area: 'testarea',
  module: null,
  kind,
  whenNotSet: 'quiet',
  actsAs: null,
  minimum: null,
  maximum: null,
  decimals: 2,
  unit: null,
  choices: ['a', 'b'],
  checklistItem: false,
  checklistBlocks: null,
  choiceRequires: [],
  divides: null,
  multipleOf: null,
})

describe('the settings screen’s value drafts (KAN-805)', () => {
  it('an empty draft is «not set» — never zero, never off', () => {
    for (const kind of ['switch', 'number', 'amount', 'choice']) expect(fromDraft(declaration(kind), '  ')).toBeNull()
    expect(toDraft(declaration('switch'), null)).toBe('')
  })

  it('a number travels as a plain decimal string: grouping and a decimal comma are removed', () => {
    expect(fromDraft(declaration('amount'), "1'250,50")).toEqual({ number: '1250.50' })
    expect(fromDraft(declaration('number'), '12.5')).toEqual({ number: '12.5' })
  })

  it('a switch and a choice round-trip', () => {
    expect(fromDraft(declaration('switch'), toDraft(declaration('switch'), { switch: false }))).toEqual({ switch: false })
    expect(fromDraft(declaration('choice'), toDraft(declaration('choice'), { choice: 'b' }))).toEqual({ choice: 'b' })
  })
})
