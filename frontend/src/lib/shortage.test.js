import { describe, expect, it } from 'vitest'
import { countShortagesBySeverity } from './shortage.js'

describe('countShortagesBySeverity', () => {
  it('counts all persisted shortage severity bands', () => {
    expect(countShortagesBySeverity([
      { severity: 'low' },
      { severity: 'medium' },
      { severity: 'medium' },
      { severity: 'high' },
      { severity: 'critical' },
    ])).toEqual({
      critical: 1,
      high: 1,
      medium: 2,
      low: 1,
    })
  })

  it('includes zero counts for severity bands without shortages', () => {
    expect(countShortagesBySeverity([{ severity: 'low' }])).toEqual({
      critical: 0,
      high: 0,
      medium: 0,
      low: 1,
    })
  })
})
