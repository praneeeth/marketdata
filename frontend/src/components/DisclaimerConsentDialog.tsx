import { useState } from 'react'
import { Dialog, DialogContent, DialogDescription, DialogHeader, DialogTitle } from '@panwatch/base-ui/components/ui/dialog'
import { Button } from '@panwatch/base-ui/components/ui/button'
import { useCompliance } from '@/hooks/use-compliance'

/**
 * Blocks the app until the current disclaimer version is acknowledged (stored server-side).
 * Shown at onboarding and again whenever the disclaimer text changes.
 */
export default function DisclaimerConsentDialog() {
  const { status, disclaimerAcknowledged, ackLoaded, acknowledgeDisclaimer } = useCompliance()
  const open = ackLoaded && !disclaimerAcknowledged
  const [checked, setChecked] = useState(false)
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState('')

  const version = status?.disclaimer.version
  const longText = status?.disclaimer.long

  const accept = async () => {
    if (!version) return
    setSaving(true)
    setError('')
    try {
      await acknowledgeDisclaimer(version)
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Could not save your acknowledgement. Please try again.')
    } finally {
      setSaving(false)
    }
  }

  return (
    <Dialog open={open} onOpenChange={() => { /* cannot be dismissed without accepting */ }}>
      <DialogContent className="max-w-lg" data-testid="disclaimer-consent">
        <DialogHeader>
          <DialogTitle>Before you continue</DialogTitle>
          <DialogDescription>Please read and acknowledge this notice.</DialogDescription>
        </DialogHeader>
        <p className="text-[13px] leading-relaxed text-foreground">{longText || 'Loading…'}</p>
        <label className="flex items-start gap-2 text-[13px] text-foreground">
          <input
            type="checkbox"
            className="mt-0.5"
            checked={checked}
            onChange={(e) => setChecked(e.target.checked)}
            aria-label="I understand this is not investment advice"
          />
          <span>I understand this service provides research and education only and is not investment advice.</span>
        </label>
        {error && <p className="text-[12px] text-destructive">{error}</p>}
        <div className="flex justify-end">
          <Button onClick={accept} disabled={!checked || !version || saving}>
            {saving ? 'Saving…' : 'I understand'}
          </Button>
        </div>
      </DialogContent>
    </Dialog>
  )
}
