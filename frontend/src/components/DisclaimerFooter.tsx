import { Info } from 'lucide-react'
import { useCompliance } from '@/hooks/use-compliance'

/** Persistent disclaimer shown on every page, including the login page. Never hidden. */
export default function DisclaimerFooter({ className = '' }: { className?: string }) {
  const { shortDisclaimer } = useCompliance()
  return (
    <footer role="contentinfo" data-testid="disclaimer-footer" className={`mx-auto w-full max-w-6xl px-4 py-5 md:px-6 ${className}`}>
      <p className="note flex items-start justify-center gap-1.5 px-3 py-2 text-left text-[11px] leading-relaxed md:text-center">
        <Info className="mt-0.5 h-3 w-3 shrink-0" aria-hidden="true" />
        <span>{shortDisclaimer}</span>
      </p>
    </footer>
  )
}
