import { ArrowUpRight, Briefcase, Search, Sparkles } from 'lucide-react'
import { useState } from 'react'

interface AssistantWelcomeProps {
  onSubmit: (question: string) => void
  disabled?: boolean
}

const QUICK_QUESTIONS = [
  { label: 'Analyse a stock', question: "Analyse a stock's fundamentals, price action and recent news", icon: Search },
  { label: 'Check my holdings', question: 'Give my portfolio a full portfolio check: risks and key points to watch', icon: Briefcase },
  { label: "Find today's ideas", question: "Using today's market, help me find ideas worth researching", icon: Sparkles },
]

/** First-run surface for the full-page assistant before a conversation exists. */
export function AssistantWelcome({ onSubmit, disabled = false }: AssistantWelcomeProps) {
  const [question, setQuestion] = useState('')

  const submit = (nextQuestion = question) => {
    const content = nextQuestion.trim()
    if (!content || disabled) return
    setQuestion('')
    onSubmit(content)
  }

  return (
    <section className="flex min-h-[calc(100vh-12rem)] flex-1 flex-col items-center justify-center px-5 py-12 text-center md:px-10">
      <p className="mb-4 text-[11px] font-semibold tracking-[0.16em] text-primary sm:text-[12px]">
        PANWATCH · AI INVESTING RESEARCH
      </p>
      <h1 className="text-3xl font-bold tracking-tight text-foreground sm:text-4xl md:text-5xl">
        What do you want to research today?
      </h1>
      <p className="mt-5 max-w-2xl text-[15px] leading-7 text-muted-foreground md:text-[17px]">
        Enter a stock, a market question, or let PanWatch check your holdings. The assistant looks up the available data first, then gives grounded conclusions.
      </p>

      <form
        className="mt-9 flex w-full max-w-3xl items-center gap-2 rounded-2xl border border-border/70 bg-background p-2 shadow-[0_18px_50px_-32px_hsl(var(--foreground)/0.5)] transition-shadow focus-within:ring-2 focus-within:ring-primary/20"
        onSubmit={(event) => {
          event.preventDefault()
          submit()
        }}
      >
        <Search className="ml-3 h-5 w-5 shrink-0 text-muted-foreground" />
        <input
          value={question}
          onChange={(event) => setQuestion(event.target.value)}
          disabled={disabled}
          className="h-12 min-w-0 flex-1 bg-transparent text-[15px] text-foreground outline-none placeholder:text-muted-foreground/80"
          placeholder="Search a stock, or ask: how risky are my holdings?"
          aria-label="Start a research question"
        />
        <button
          type="submit"
          disabled={disabled || !question.trim()}
          className="flex h-10 w-10 shrink-0 items-center justify-center rounded-xl bg-primary text-primary-foreground transition-colors hover:bg-primary/90 disabled:cursor-not-allowed disabled:opacity-40"
          aria-label="Send the research question"
        >
          <ArrowUpRight className="h-4 w-4" />
        </button>
      </form>

      <div className="mt-7 flex flex-wrap justify-center gap-2.5">
        {QUICK_QUESTIONS.map(({ label, question: quickQuestion, icon: Icon }) => (
          <button
            key={label}
            type="button"
            disabled={disabled}
            onClick={() => submit(quickQuestion)}
            className="inline-flex items-center gap-2 rounded-xl border border-border/60 bg-card px-4 py-2.5 text-[13px] font-medium text-foreground shadow-sm transition-all hover:-translate-y-0.5 hover:border-primary/30 hover:bg-primary/5 disabled:cursor-not-allowed disabled:opacity-50"
          >
            <Icon className="h-3.5 w-3.5 text-primary" />
            {label}
          </button>
        ))}
      </div>

      <div className="mt-16 grid w-full max-w-3xl gap-3 text-left sm:grid-cols-3">
        {[
          ['01', 'Start from a stock', 'Enter a symbol or company name for an overall, short-term or event-driven analysis.'],
          ['02', 'Start from your holdings', 'Uses your real and simulation holdings to spot concentration and risk exposure.'],
          ['03', 'Start from a question', 'The assistant ties together quotes, K-lines and news and suggests where to research next.'],
        ].map(([index, title, description]) => (
          <div key={index} className="rounded-2xl border border-border/60 bg-card/70 p-5">
            <span className="inline-flex rounded-lg bg-primary/10 px-2 py-1 text-[12px] font-semibold text-primary">{index}</span>
            <h2 className="mt-5 text-[15px] font-semibold text-foreground">{title}</h2>
            <p className="mt-2 text-[12px] leading-5 text-muted-foreground">{description}</p>
          </div>
        ))}
      </div>
    </section>
  )
}
