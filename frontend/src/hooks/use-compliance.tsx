import { createContext, useContext, useEffect, useMemo, useState } from 'react'
import type { ReactNode } from 'react'
import {
  complianceApi,
  FALLBACK_SHORT_DISCLAIMER,
  type ComplianceFeature,
  type ComplianceStatus,
} from '@panwatch/api/compliance'

interface ComplianceContextValue {
  status: ComplianceStatus | null
  shortDisclaimer: string
  /** Fails closed: every gated feature is off until the server says otherwise. */
  isEnabled: (feature: ComplianceFeature) => boolean
}

const ComplianceContext = createContext<ComplianceContextValue>({
  status: null,
  shortDisclaimer: FALLBACK_SHORT_DISCLAIMER,
  isEnabled: () => false,
})

export function ComplianceProvider({ children }: { children: ReactNode }) {
  const [status, setStatus] = useState<ComplianceStatus | null>(null)

  useEffect(() => {
    let active = true
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

  const value = useMemo<ComplianceContextValue>(
    () => ({
      status,
      shortDisclaimer: status?.disclaimer.short || FALLBACK_SHORT_DISCLAIMER,
      isEnabled: (feature) => status?.features?.[feature] === true,
    }),
    [status],
  )

  return <ComplianceContext.Provider value={value}>{children}</ComplianceContext.Provider>
}

export function useCompliance(): ComplianceContextValue {
  return useContext(ComplianceContext)
}
