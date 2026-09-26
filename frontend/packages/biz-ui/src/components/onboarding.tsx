import { useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { TrendingUp, Bot, Bell, CheckCircle2, ChevronRight, Sparkles } from 'lucide-react'
import { Dialog, DialogContent } from '@candlewise/base-ui/components/ui/dialog'
import { Button } from '@candlewise/base-ui/components/ui/button'
import { useCompliance } from '@/hooks/use-compliance'

interface OnboardingProps {
  open: boolean
  onComplete: () => void
  hasStocks: boolean
}

type Step = 'welcome' | 'ai' | 'notify' | 'complete'

export function Onboarding({ open, onComplete, hasStocks }: OnboardingProps) {
  const navigate = useNavigate()
  const { shortDisclaimer } = useCompliance()
  const [step, setStep] = useState<Step>('welcome')

  const handleNext = () => {
    if (step === 'welcome') {
      setStep('ai')
    } else if (step === 'ai') {
      setStep('notify')
    } else if (step === 'notify') {
      setStep('complete')
    } else {
      onComplete()
    }
  }

  const handleSkip = () => {
    onComplete()
  }

  const handleGoToSettings = () => {
    onComplete()
    navigate('/settings')
  }

  const handleGoToPortfolio = () => {
    onComplete()
    navigate('/portfolio')
  }

  return (
    <Dialog open={open} onOpenChange={(open) => !open && onComplete()}>
      <DialogContent className="max-w-md p-0 overflow-hidden">
        {/* Progress Indicator */}
        <div className="flex items-center gap-1.5 px-6 pt-6">
          {(['welcome', 'ai', 'notify', 'complete'] as Step[]).map((s, i) => (
            <div
              key={s}
              className={`flex-1 h-1 rounded-full transition-colors ${
                i <= ['welcome', 'ai', 'notify', 'complete'].indexOf(step)
                  ? 'bg-primary'
                  : 'bg-accent/50'
              }`}
            />
          ))}
        </div>

        <div className="p-6 pt-4">
          {step === 'welcome' && (
            <div className="text-center">
              <div className="w-16 h-16 rounded-2xl bg-primary flex items-center justify-center mx-auto mb-4">
                <TrendingUp className="w-8 h-8 text-white" />
              </div>
              <h2 className="text-[20px] font-bold text-foreground mb-2">
                Welcome to Candlewise
              </h2>
              <p className="text-[14px] text-muted-foreground mb-6">
                {hasStocks
                  ? 'Your watchlist is ready; you can start now'
                  : "We've added 5 popular NSE stocks as examples so you can see live quotes right away"
                }
              </p>

              <div className="space-y-3 text-left mb-6">
                <div className="flex items-start gap-3 p-3 rounded-xl bg-accent/30">
                  <div className="w-8 h-8 rounded-lg bg-blue-500/10 flex items-center justify-center flex-shrink-0">
                    <TrendingUp className="w-4 h-4 text-blue-500" />
                  </div>
                  <div>
                    <p className="text-[13px] font-medium text-foreground">Live quote monitoring</p>
                    <p className="text-[12px] text-muted-foreground">Track watchlist prices and spot unusual moves quickly</p>
                  </div>
                </div>
                <div className="flex items-start gap-3 p-3 rounded-xl bg-accent/30">
                  <div className="w-8 h-8 rounded-lg bg-primary/10 flex items-center justify-center flex-shrink-0">
                    <Bot className="w-4 h-4 text-primary" />
                  </div>
                  <div>
                    <p className="text-[13px] font-medium text-foreground">AI analysis</p>
                    <p className="text-[12px] text-muted-foreground">Daily close reports, move watch, technical analysis</p>
                  </div>
                </div>
                <div className="flex items-start gap-3 p-3 rounded-xl bg-accent/30">
                  <div className="w-8 h-8 rounded-lg bg-amber-500/10 flex items-center justify-center flex-shrink-0">
                    <Bell className="w-4 h-4 text-amber-500" />
                  </div>
                  <div>
                    <p className="text-[13px] font-medium text-foreground">Smart notifications</p>
                    <p className="text-[12px] text-muted-foreground">Sent to Telegram, Discord and more</p>
                  </div>
                </div>
              </div>

              <div className="flex items-center gap-3">
                <Button className="flex-1" onClick={handleNext}>
                  Get started <ChevronRight className="w-4 h-4" />
                </Button>
              </div>
              <button
                onClick={handleSkip}
                className="mt-3 text-[12px] text-muted-foreground hover:text-foreground transition-colors"
              >
                Skip the tour
              </button>
            </div>
          )}

          {step === 'ai' && (
            <div className="text-center">
              <div className="w-16 h-16 rounded-2xl bg-primary flex items-center justify-center mx-auto mb-4">
                <Bot className="w-8 h-8 text-white" />
              </div>
              <h2 className="text-[20px] font-bold text-foreground mb-2">
                Set up AI analysis
              </h2>
              <p className="text-[14px] text-muted-foreground mb-4">
                Connect an AI service to turn on AI analysis
              </p>

              <div className="space-y-2 text-left mb-6 p-4 rounded-xl bg-accent/30">
                <div className="flex items-center gap-2 text-[13px]">
                  <Sparkles className="w-4 h-4 text-primary" />
                  <span className="text-foreground">Automatic daily close reports</span>
                </div>
                <div className="flex items-center gap-2 text-[13px]">
                  <Sparkles className="w-4 h-4 text-primary" />
                  <span className="text-foreground">AI watch on unusual moves</span>
                </div>
                <div className="flex items-center gap-2 text-[13px]">
                  <Sparkles className="w-4 h-4 text-primary" />
                  <span className="text-foreground">Technical chart analysis</span>
                </div>
              </div>

              <p className="text-[12px] text-muted-foreground mb-4">
                Supports OpenAI, Anthropic, DeepSeek and more
              </p>

              <div className="flex items-center gap-3">
                <Button variant="secondary" className="flex-1" onClick={handleNext}>
                  Later
                </Button>
                <Button className="flex-1" onClick={handleGoToSettings}>
                  Set up
                </Button>
              </div>
            </div>
          )}

          {step === 'notify' && (
            <div className="text-center">
              <div className="w-16 h-16 rounded-2xl bg-amber-500 flex items-center justify-center mx-auto mb-4">
                <Bell className="w-8 h-8 text-white" />
              </div>
              <h2 className="text-[20px] font-bold text-foreground mb-2">
                Set up notification channels
              </h2>
              <p className="text-[14px] text-muted-foreground mb-4">
                Get live notifications once set up
              </p>

              <div className="space-y-2 text-left mb-6 p-4 rounded-xl bg-accent/30">
                <div className="flex items-center gap-2 text-[13px]">
                  <Bell className="w-4 h-4 text-amber-500" />
                  <span className="text-foreground">Intraday move alerts</span>
                </div>
                <div className="flex items-center gap-2 text-[13px]">
                  <Bell className="w-4 h-4 text-amber-500" />
                  <span className="text-foreground">AI report notifications</span>
                </div>
                <div className="flex items-center gap-2 text-[13px]">
                  <Bell className="w-4 h-4 text-amber-500" />
                  <span className="text-foreground">Price level alerts</span>
                </div>
              </div>

              <p className="text-[12px] text-muted-foreground mb-4">
                Supports Telegram, Discord and Pushover
              </p>

              <div className="flex items-center gap-3">
                <Button variant="secondary" className="flex-1" onClick={handleNext}>
                  Later
                </Button>
                <Button className="flex-1" onClick={handleGoToSettings}>
                  Set up
                </Button>
              </div>
            </div>
          )}

          {step === 'complete' && (
            <div className="text-center">
              <div className="w-16 h-16 rounded-2xl bg-emerald-500 flex items-center justify-center mx-auto mb-4">
                <CheckCircle2 className="w-8 h-8 text-white" />
              </div>
              <h2 className="text-[20px] font-bold text-foreground mb-2">
                All set
              </h2>
              <p className="text-[14px] text-muted-foreground mb-6">
                You can change these any time on the Settings page
              </p>

              <div className="space-y-3">
                <Button className="w-full" onClick={() => onComplete()}>
                  Go to the dashboard
                </Button>
                <Button variant="secondary" className="w-full" onClick={handleGoToPortfolio}>
                  Manage the watchlist
                </Button>
              </div>
            </div>
          )}

          <p
            className="mt-5 border-t border-border/40 pt-3 text-[11px] leading-snug text-muted-foreground"
            data-testid="onboarding-disclaimer"
          >
            {shortDisclaimer}
          </p>
        </div>
      </DialogContent>
    </Dialog>
  )
}
