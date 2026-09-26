import { describe, expect, it } from 'vitest'
import { isUnstructured, parseResearchSummary, toItems } from './research-summary'

const SAMPLE = `### Summary
Infosys reported steady revenue with **margin pressure**. Deal wins stayed strong.

### Bull case
- Large deal TCV of $2.4bn in the quarter
- Net cash balance sheet
  with room for buybacks

### Bear case
1. Discretionary spending remains weak
2. Pricing pressure in BFSI

### Key risks
- Rupee appreciation
- Client concentration

### Technical levels (descriptive)
- Support near 991.6 (recent low)
- Resistance around 1,156 (20-day high)

### Upcoming events
- Q2 results on 16 Oct 2026

### Sources
- Market report, 25 Sep 2026
- News report, 26 Sep 2026

### Analyst notes
Something extra.`

describe('parseResearchSummary', () => {
  it('splits the fixed sections into items', () => {
    const r = parseResearchSummary(SAMPLE)
    expect(r.summary).toBe('Infosys reported steady revenue with margin pressure. Deal wins stayed strong.')
    expect(r.bull).toEqual(['Large deal TCV of $2.4bn in the quarter', 'Net cash balance sheet with room for buybacks'])
    expect(r.bear).toEqual(['Discretionary spending remains weak', 'Pricing pressure in BFSI'])
    expect(r.risks).toEqual(['Rupee appreciation', 'Client concentration'])
    expect(r.levels).toHaveLength(2)
    expect(r.events).toEqual(['Q2 results on 16 Oct 2026'])
    expect(r.sources).toEqual(['Market report, 25 Sep 2026', 'News report, 26 Sep 2026'])
    expect(r.other).toEqual([{ title: 'Analyst notes', body: 'Something extra.' }])
    expect(isUnstructured(r)).toBe(false)
  })

  it('treats text without headings as the summary', () => {
    const r = parseResearchSummary('Just a paragraph.\n\nAnd another.')
    expect(r.summary).toBe('Just a paragraph. And another.')
    expect(isUnstructured(r)).toBe(true)
  })

  it('handles empty input and "none" items', () => {
    expect(parseResearchSummary(null).summary).toBe('')
    expect(parseResearchSummary('### Upcoming events\n- None').events).toEqual([])
    expect(toItems('- a\n- N/A')).toEqual(['a'])
  })
})
