import { describe, expect, it } from 'vitest'
import { contrastRatio, judgeBrandColors } from './contrast'

// KAN-205 — the same vectors as tests/test_branding.py::test_the_contrast_ratio_is_wcags,
// so the verdict shown while picking is the one the save returns.
const VECTORS: [string, string, number][] = [
  ['#000000', '#FFFFFF', 21.0],
  ['#FFFFFF', '#FFFFFF', 1.0],
  ['#7C3AED', '#FFFFFF', 5.6986],
  ['#767676', '#FFFFFF', 4.5422],
  ['#777777', '#FFFFFF', 4.4781],
  ['#FACC15', '#FFFFFF', 1.5314],
  ['#FACC15', '#1E3A8A', 6.7637],
]

describe('contrast (KAN-205)', () => {
  it.each(VECTORS)('%s on %s is %f:1, either way round', (a, b, ratio) => {
    expect(contrastRatio(a, b)).toBeCloseTo(ratio, 4)
    expect(contrastRatio(b, a)).toBeCloseTo(ratio, 4)
  })

  it('judges AA on the unrounded ratio', () => {
    expect(judgeBrandColors('#767676', null)[0].passes).toBe(true)
    expect(judgeBrandColors('#777777', null)[0].passes).toBe(false)
  })

  it('judges the secondary against white and against the primary, as the API does', () => {
    expect(judgeBrandColors('#facc15', '#1E3A8A')).toEqual([
      { pair: 'primary_on_white', foreground: '#FACC15', background: null, ratio: 1.53, passes: false },
      { pair: 'secondary_on_white', foreground: '#1E3A8A', background: null, ratio: 10.36, passes: true },
      { pair: 'secondary_on_primary', foreground: '#1E3A8A', background: '#FACC15', ratio: 6.76, passes: true },
    ])
  })

  it('judges nothing it cannot read yet', () => {
    expect(judgeBrandColors('#12', null)).toEqual([])
    expect(judgeBrandColors('#7C3AED', '#1')).toHaveLength(1)
  })
})
