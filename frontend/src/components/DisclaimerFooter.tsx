import { useCompliance } from '@/hooks/use-compliance'

/** Persistent disclaimer shown on every page, including the login page. */
export default function DisclaimerFooter({ className = '' }: { className?: string }) {
  const { shortDisclaimer } = useCompliance()
  return (
    <footer
      role="contentinfo"
      data-testid="disclaimer-footer"
      className={`mx-auto max-w-5xl px-4 py-4 text-center text-[11px] leading-relaxed text-muted-foreground ${className}`}
    >
      {shortDisclaimer}
    </footer>
  )
}
