/**
 * Parse the deep-research summary Markdown into its fixed sections.
 *
 * The research-only summary node (src/modules/automation/tradingagents/research_graph.py)
 * asks the model for exactly these `###` sections: Summary, Bull case, Bear case, Key risks,
 * Technical levels (descriptive), Upcoming events, Sources. Models drift, so matching is
 * by keyword and anything unrecognised is kept in `other` rather than dropped.
 */

export interface ParsedResearch {
  summary: string
  bull: string[]
  bear: string[]
  risks: string[]
  levels: string[]
  events: string[]
  sources: string[]
  other: Array<{ title: string; body: string }>
}

type Key = Exclude<keyof ParsedResearch, 'other'>

const MATCHERS: Array<[Key, RegExp]> = [
  ['summary', /^(summary|overview|tl;?dr)\b/i],
  ['bull', /\bbull/i],
  ['bear', /\bbear/i],
  ['risks', /\brisk/i],
  ['levels', /\b(technical|levels?|support|resistance)\b/i],
  ['events', /\b(event|catalyst|calendar|upcoming)/i],
  ['sources', /\b(source|reference|data used)/i],
]

const BULLET = /^\s*(?:[-*•]|\d+[.)])\s+/

function cleanInline(text: string): string {
  return text
    .replace(/\*\*(.+?)\*\*/g, '$1')
    .replace(/__(.+?)__/g, '$1')
    .replace(/`([^`]+)`/g, '$1')
    .trim()
}

/** Split a section body into items: bullet lines, or paragraphs when there are no bullets. */
export function toItems(body: string): string[] {
  const lines = body.split('\n')
  const hasBullets = lines.some((l) => BULLET.test(l))
  if (!hasBullets) {
    return body
      .split(/\n\s*\n/)
      .map((p) => cleanInline(p.replace(/\s*\n\s*/g, ' ')))
      .filter(Boolean)
  }
  const items: string[] = []
  for (const line of lines) {
    if (BULLET.test(line)) items.push(line.replace(BULLET, ''))
    else if (line.trim() && items.length) items[items.length - 1] += ` ${line.trim()}`
  }
  return items.map(cleanInline).filter((s) => s && !/^(none|n\/a|not mentioned\.?)$/i.test(s))
}

export function parseResearchSummary(markdown: string | null | undefined): ParsedResearch {
  const out: ParsedResearch = { summary: '', bull: [], bear: [], risks: [], levels: [], events: [], sources: [], other: [] }
  const text = (markdown || '').replace(/\r\n/g, '\n').trim()
  if (!text) return out

  const parts = text.split(/^#{2,4}\s+/m)
  const preamble = parts.shift()?.trim() || ''
  if (preamble) out.summary = toItems(preamble).join(' ')

  for (const part of parts) {
    const newline = part.indexOf('\n')
    const title = cleanInline(newline === -1 ? part : part.slice(0, newline)).replace(/[:.]$/, '')
    const body = newline === -1 ? '' : part.slice(newline + 1).trim()
    const match = MATCHERS.find(([, re]) => re.test(title))
    if (!match) {
      if (body) out.other.push({ title, body })
      continue
    }
    const key = match[0]
    if (key === 'summary') out.summary = [out.summary, toItems(body).join(' ')].filter(Boolean).join(' ')
    else out[key].push(...toItems(body))
  }
  return out
}

/** True when the parse found none of the structured sections (the UI then shows the raw text). */
export function isUnstructured(parsed: ParsedResearch): boolean {
  return !parsed.bull.length && !parsed.bear.length && !parsed.risks.length && !parsed.events.length && !parsed.levels.length
}
