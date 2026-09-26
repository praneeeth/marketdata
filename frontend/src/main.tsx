import React from 'react'
import ReactDOM from 'react-dom/client'
import { BrowserRouter } from 'react-router-dom'
import App from './App'
import { ToastProvider } from '@candlewise/base-ui/components/ui/toast'
import { ComplianceProvider } from '@/hooks/use-compliance'
import './index.css'
import { migrateLegacyStorage } from '@/lib/brand'

migrateLegacyStorage()

ReactDOM.createRoot(document.getElementById('root')!).render(
  <React.StrictMode>
    <BrowserRouter>
      <ToastProvider>
        <ComplianceProvider>
          <App />
        </ComplianceProvider>
      </ToastProvider>
    </BrowserRouter>
  </React.StrictMode>
)
