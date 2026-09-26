import { createContext, useCallback, useContext, useEffect, useMemo, useState } from 'react'
import type { ReactNode } from 'react'
import {
  complianceApi,
  FALLBACK_SHORT_DISCLAIMER,
  type ComplianceFeature,
  type ComplianceStatus,
} from '@candlewise/api/compliance'

interface ComplianceContextValue {
  status: ComplianceStatus | null
  shortDisclaimer: string
  /** Fails closed: every gated feature is off until the server says otherwise. */
  isEnabled: (feature: ComplianceFeature) => boolean
  /** Fails closed: false until the server confirms the current disclaimer version is acknowledged. */
  disclaimerAcknowledged: boolean
  /** True once the acknowledgement check has finished (successfully or not). */
  ackLoaded: boolean
  acknowledgeDisclaimer: (version: string) => Promise<void>
}

const ComplianceContext = createContext<ComplianceContextValue>({
  status: null,
  shortDisclaimer: FALLBACK_SHORT_DISCLAIMER,
  isEnabled: () => false,
  disclaimerAcknowledged: false,
  ackLoaded: false,
  acknowledgeDisclaimer: async () => {},
})

export function ComplianceProvider({ children }: { children: ReactNode }) {
  const [status, setStatus] = useState<ComplianceStatus | null>(null)
  const [disclaimerAcknowledged, setDisclaimerAcknowledged] = useState(false)
  const [ackLoaded, setAckLoaded] = useState(false)

  useEffect(() => {
    let active = true
    complianceApi
      .getAck()
      .then((ack) => {
        if (active) setDisclaimerAcknowledged(!ack.required)
      })
      .catch(() => {
        // Keep the fail-closed default: ask for acknowledgement again.
      })
      .finally(() => {
        if (active) setAckLoaded(true)
      })
    complianceApi
      .status()
      .then((data) => {
        if (active) setStatus(data)
      })
      .catch(() => {
        // Keep the fail-closed defaults.
      })
    return () => {
      active = false
    }
  }, [])

  const acknowledgeDisclaimer = useCallback(async (version: string) => {
    await complianceApi.acknowledge(version)
    setDisclaimerAcknowledged(true)
  }, [])

  const value = useMemo<ComplianceContextValue>(
    () => ({
      status,
      shortDisclaimer: status?.disclaimer.short || FALLBACK_SHORT_DISCLAIMER,
      isEnabled: (feature) => status?.features?.[feature] === true,
      disclaimerAcknowledged,
      ackLoaded,
      acknowledgeDisclaimer,
    }),
    [status, disclaimerAcknowledged, ackLoaded, acknowledgeDisclaimer],
  )

  return <ComplianceContext.Provider value={value}>{children}</ComplianceContext.Provider>
}

export function useCompliance(): ComplianceContextValue {
  return useContext(ComplianceContext)
}
