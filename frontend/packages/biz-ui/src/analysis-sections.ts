import type { DeepAnalysisResult } from '@candlewise/api'

export interface AnalysisSection {
  id: string
  title: string
  markdown: string
}

/**
 * Assemble the sections from deep research raw_data (decision text / four analysts / bull vs bear debate / risk debate).
 * The dialog's tabs and the detail reading page share this logic, so the two renderings don't drift apart.
 * The order is the detail page's top-to-bottom and the dialog tabs' left-to-right order. Only sections with content are returned.
 */
export function buildAnalysisSections(
  rawData: Partial<DeepAnalysisResult['raw_data']>,
  options: { includeDecision?: boolean } = {},
): AnalysisSection[] {
  // Fail closed: the decision, trader plan and risk debate are only shown when the
  // caller says rating is enabled (ADR-005). Research-only runs never produce them.
  const includeDecision = options.includeDecision === true
  const reports = rawData.analyst_reports || { market: '', social: '', news: '', fundamentals: '' }
  const debate = rawData.debate_history
  const riskDebate = includeDecision ? rawData.risk_debate : undefined
  const sections: AnalysisSection[] = []

  if (rawData.research_summary) {
    sections.push({ id: 'summary', title: 'Research summary', markdown: rawData.research_summary })
  }

  // Decision: the section title is simply "PM decision" (the old duplicated leading "Final decision" heading is gone);
  // the trader's plan stays as a sub-heading (to set it apart from the decision).
  const decisionBody = includeDecision
    ? [
        rawData.final_decision || '',
        rawData.trader_plan && `### 💼 Trader's plan\n\n${rawData.trader_plan}`,
      ]
        .filter(Boolean)
        .join('\n\n')
    : ''
  if (decisionBody) sections.push({ id: 'decision', title: 'PM decision', markdown: decisionBody })

  // The four analysts
  const analysts: [string, string][] = [
    ['market', 'Technical analyst'],
    ['social', 'Sentiment analyst'],
    ['news', 'News analyst'],
    ['fundamentals', 'Fundamentals analyst'],
  ]
  for (const [k, title] of analysts) {
    const text = (reports as unknown as Record<string, string>)[k] || ''
    if (text) sections.push({ id: k, title, markdown: text })
  }

  // Bull vs bear debate (research team: debate history + research manager's ruling)
  if (debate?.history) {
    let dc = debate.history
    if (includeDecision && debate.judge_decision) dc += `\n\n### ⚖️ Research manager's ruling\n\n${debate.judge_decision}`
    sections.push({ id: 'debate', title: 'Bull vs bear debate', markdown: dc })
  }

  // Risk debate (risk team: aggressive/neutral/conservative debate + risk ruling)
  if (riskDebate?.history) {
    let rc = riskDebate.history
    if (riskDebate.judge_decision) rc += `\n\n### 🛡️ Risk ruling\n\n${riskDebate.judge_decision}`
    sections.push({ id: 'risk', title: 'Risk debate', markdown: rc })
  }

  return sections
}
