import { useRef, useState, type ReactNode } from 'react'
import { toPng } from 'html-to-image'
import { ImageDown, Loader2 } from 'lucide-react'
import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
  DialogDescription,
} from '@panwatch/base-ui/components/ui/dialog'
import { Button } from '@panwatch/base-ui/components/ui/button'
import { useCompliance } from '@/hooks/use-compliance'

interface ShareCardDialogProps {
  open: boolean
  onClose: () => void
  /** Exported PNG file name (without the extension). */
  filename: string
  /** Fixed card width, 640 by default. */
  width?: number
  /** The card's face, supplied by each card. Colours must be inline and explicit, not theme CSS variables. */
  children: ReactNode
}

/**
 * Shared share-card shell: one Dialog + a fixed-width card container + brand footer + a "Download image" button.
 *
 * Design notes:
 * - The card container has a fixed width (640px by default), its own white -> #f8fafc gradient, rounded corners, padding, system font and explicit dark text,
 *   so the exported PNG looks the same in any theme (light/dark). Each card only supplies its face as children.
 * - The footer (disclaimer + PanWatch · GitHub line) is rendered by the shell, as the consistent anchor for every share card.
 * - "Download image" exports ${filename}.png with html-to-image's toPng (pixelRatio: 2, cacheBust: true).
 */
function ShareCardDialogInner({
  open,
  onClose,
  filename,
  width = 640,
  children,
}: ShareCardDialogProps) {
  const cardRef = useRef<HTMLDivElement>(null)
  const [busy, setBusy] = useState(false)

  const handleDownload = async () => {
    if (busy || !cardRef.current) return
    setBusy(true)
    try {
      const dataUrl = await toPng(cardRef.current, { pixelRatio: 2, cacheBust: true })
      const link = document.createElement('a')
      link.download = `${filename}.png`
      link.href = dataUrl
      link.click()
    } catch (e) {
      alert(e instanceof Error ? `Image generation failed: ${e.message}` : 'Image generation failed; please try again')
    } finally {
      setBusy(false)
    }
  }

  return (
    <Dialog open={open} onOpenChange={(v) => !v && onClose()}>
      <DialogContent className="max-w-2xl">
        <DialogHeader>
          <DialogTitle>Share image</DialogTitle>
          <DialogDescription>Export a clean card to share on social media or in group chats.</DialogDescription>
        </DialogHeader>

        {/* Preview: the outer area uses the theme background; the inner card has its own explicit colours */}
        <div className="flex justify-center overflow-x-auto rounded-xl bg-accent/30 p-4 scrollbar">
          {/* Exported card: fixed width, every colour inline, no theme CSS variables */}
          <div
            ref={cardRef}
            style={{
              width,
              boxSizing: 'border-box',
              background: 'linear-gradient(180deg, #ffffff 0%, #f8fafc 100%)',
              borderRadius: 24,
              padding: '32px 36px',
              border: '1px solid #e2e8f0',
              fontFamily:
                '-apple-system, BlinkMacSystemFont, "PingFang SC", "Microsoft YaHei", "Segoe UI", sans-serif',
              color: '#0f172a',
            }}
          >
            {/* The card's face */}
            {children}

            {/* Divider */}
            <div style={{ height: 1, background: '#e2e8f0', margin: '24px 0 16px' }} />

            {/* Footer: disclaimer + brand line (the same on every share card) */}
            <div style={{ fontSize: 12, color: '#94a3b8', lineHeight: 1.6 }}>
              For reference only; not investment advice
            </div>
            <div
              style={{
                marginTop: 8,
                display: 'flex',
                alignItems: 'center',
                gap: 8,
                fontSize: 13.5,
                fontWeight: 700,
                color: '#0f172a',
              }}
            >
              <span
                style={{
                  display: 'inline-flex',
                  alignItems: 'center',
                  justifyContent: 'center',
                  width: 22,
                  height: 22,
                  borderRadius: 6,
                  background: '#0f172a',
                  color: '#ffffff',
                  fontSize: 13,
                  fontWeight: 900,
                  flexShrink: 0,
                }}
              >
                P
              </span>
              <span>PanWatch</span>
              <span style={{ color: '#cbd5e1', fontWeight: 400 }}>·</span>
              <span style={{ color: '#64748b', fontWeight: 500, fontSize: 12.5 }}>
                github.com/TNT-Likely/PanWatch
              </span>
            </div>
          </div>
        </div>

        {/* Actions */}
        <div className="mt-4 flex items-center justify-end gap-3">
          <Button variant="outline" size="sm" className="h-9" onClick={onClose} disabled={busy}>
            Close
          </Button>
          <Button size="sm" className="h-9" onClick={() => void handleDownload()} disabled={busy}>
            {busy ? (
              <Loader2 className="w-3.5 h-3.5 animate-spin" />
            ) : (
              <ImageDown className="w-3.5 h-3.5" />
            )}
            {busy ? 'Generating…' : 'Download image'}
          </Button>
        </div>
      </DialogContent>
    </Dialog>
  )
}

function SharingDisabledDialog({ open, onClose }: { open: boolean; onClose: () => void }) {
  return (
    <Dialog open={open} onOpenChange={(v) => !v && onClose()}>
      <DialogContent className="max-w-sm" data-testid="sharing-disabled">
        <DialogHeader>
          <DialogTitle>Sharing unavailable</DialogTitle>
          <DialogDescription>
            Sharing images of AI analysis is not available in research-only mode.
          </DialogDescription>
        </DialogHeader>
      </DialogContent>
    </Dialog>
  )
}

export default function ShareCardDialog(props: ShareCardDialogProps) {
  const { isEnabled } = useCompliance()
  if (!isEnabled('share_cards')) return <SharingDisabledDialog open={props.open} onClose={props.onClose} />
  return <ShareCardDialogInner {...props} />
}
