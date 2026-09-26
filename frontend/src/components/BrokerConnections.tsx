import { useCallback, useEffect, useState } from 'react'
import { AlertTriangle, KeyRound, LogIn, Plug, Trash2, Unplug } from 'lucide-react'
import {
  brokersApi,
  DATA_QUALITY_LABEL,
  type BrokerConnection,
  type BrokerOverview,
  type BrokerProviderInfo,
  type BrokerStatus,
} from '@candlewise/api/brokers'
import { Badge } from '@candlewise/base-ui/components/ui/badge'
import { Button } from '@candlewise/base-ui/components/ui/button'
import { Input } from '@candlewise/base-ui/components/ui/input'
import { Label } from '@candlewise/base-ui/components/ui/label'

const STATUS_TEXT: Record<BrokerStatus, string> = {
  connected: 'Connected',
  disconnected: 'Not logged in',
  expired: 'Session expired, log in again',
  error: 'Login failed',
}

const STATUS_CLASS: Record<BrokerStatus, string> = {
  connected: 'bg-emerald-500/10 text-emerald-600 border-emerald-500/20',
  disconnected: 'bg-muted text-muted-foreground border-border',
  expired: 'bg-amber-500/10 text-amber-600 border-amber-500/20',
  error: 'bg-destructive/10 text-destructive border-destructive/20',
}

const FIELD_LABEL: Record<string, string> = {
  api_key: 'API key',
  api_secret: 'API secret',
  client_id: 'Client ID (API key)',
  client_secret: 'Client secret',
  redirect_uri: 'Redirect URL',
  client_code: 'Client code',
  pin: 'PIN',
}

const SECRET_FIELDS = new Set(['api_secret', 'client_secret', 'pin'])

function formatExpiry(iso: string | null): string {
  if (!iso) return ''
  return new Date(iso).toLocaleString('en-IN', {
    timeZone: 'Asia/Kolkata',
    dateStyle: 'medium',
    timeStyle: 'short',
  })
}

/** The result of a broker's login redirect, e.g. `?broker=kite&status=connected`. */
export function readLoginResult(search: string): { provider: string; status: string } | null {
  const params = new URLSearchParams(search)
  const provider = params.get('broker')
  const status = params.get('status')
  return provider && status ? { provider, status } : null
}

interface ProviderCardProps {
  info: BrokerProviderInfo
  connection: BrokerConnection | undefined
  onChanged: () => void
}

function ProviderCard({ info, connection, onChanged }: ProviderCardProps) {
  const [editing, setEditing] = useState(false)
  const [values, setValues] = useState<Record<string, string>>({})
  const [totp, setTotp] = useState('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')

  const run = async (action: () => Promise<unknown>) => {
    setBusy(true)
    setError('')
    try {
      await action()
      onChanged()
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Something went wrong. Please try again.')
    } finally {
      setBusy(false)
    }
  }

  const save = () =>
    run(async () => {
      await brokersApi.save(info.provider, values)
      setEditing(false)
      setValues({})
    })

  const startLogin = () =>
    run(async () => {
      const { login_url } = await brokersApi.startLogin(info.provider)
      window.location.assign(login_url)
    })

  const submitTotp = () =>
    run(async () => {
      await brokersApi.loginWithTotp(info.provider, totp)
      setTotp('')
    })

  const status = connection?.status
  const needsKeys = info.fields.length > 0 && (!connection || editing)
  const quality = connection?.quality ?? info.quality

  return (
    <div className="card p-4 space-y-3" data-testid={`broker-${info.provider}`}>
      <div className="flex items-start justify-between gap-3">
        <div className="min-w-0">
          <div className="flex items-center gap-2 flex-wrap">
            <span className="text-[14px] font-semibold text-foreground">{info.label}</span>
            {status && (
              <span
                className={`text-[11px] px-2 py-0.5 rounded-full border ${STATUS_CLASS[status]}`}
                data-testid={`broker-status-${info.provider}`}
              >
                {STATUS_TEXT[status]}
              </span>
            )}
            {quality === 'unofficial_delayed' && (
              <Badge variant="outline" className="text-[10px] border-amber-500/40 text-amber-600">
                {DATA_QUALITY_LABEL[quality]}
              </Badge>
            )}
          </div>
          <p className="text-[12px] text-muted-foreground mt-1">{info.notes}</p>
          {connection?.key_hint && (
            <p className="text-[12px] text-muted-foreground mt-1">
              <KeyRound className="inline w-3 h-3 mr-1" />
              {connection.key_hint}
            </p>
          )}
          {status === 'connected' && connection?.session_expires_at && (
            <p className="text-[12px] text-muted-foreground mt-1">
              Session valid until {formatExpiry(connection.session_expires_at)} IST
            </p>
          )}
          {connection?.last_error && (
            <p className="text-[12px] text-destructive mt-1">{connection.last_error}</p>
          )}
        </div>
        {connection && (
          <div className="flex gap-1 flex-shrink-0">
            {info.fields.length > 0 && !editing && (
              <Button variant="ghost" size="sm" className="h-7 text-[12px]" onClick={() => setEditing(true)}>
                Edit keys
              </Button>
            )}
            {status === 'connected' && info.login !== 'none' && (
              <Button
                variant="ghost"
                size="sm"
                className="h-7 text-[12px]"
                disabled={busy}
                onClick={() => run(() => brokersApi.disconnect(info.provider))}
              >
                <Unplug className="w-3.5 h-3.5 mr-1" />
                Log out
              </Button>
            )}
            <Button
              variant="ghost"
              size="sm"
              className="h-7 text-[12px] text-destructive"
              disabled={busy}
              aria-label={`Remove ${info.label}`}
              onClick={() => run(() => brokersApi.remove(info.provider))}
            >
              <Trash2 className="w-3.5 h-3.5" />
            </Button>
          </div>
        )}
      </div>

      {needsKeys && (
        <div className="grid gap-2 sm:grid-cols-2">
          {info.fields.map((field) => (
            <div key={field} className="space-y-1">
              <Label htmlFor={`${info.provider}-${field}`} className="text-[12px]">
                {FIELD_LABEL[field] ?? field}
              </Label>
              <Input
                id={`${info.provider}-${field}`}
                type={SECRET_FIELDS.has(field) ? 'password' : 'text'}
                autoComplete="off"
                placeholder={connection ? 'Leave blank to keep the saved value' : ''}
                value={values[field] ?? ''}
                onChange={(e) => setValues({ ...values, [field]: e.target.value })}
              />
            </div>
          ))}
          <div className="sm:col-span-2 flex gap-2">
            <Button size="sm" onClick={save} disabled={busy}>
              <Plug className="w-3.5 h-3.5 mr-1" />
              Save
            </Button>
            {editing && (
              <Button size="sm" variant="ghost" onClick={() => { setEditing(false); setValues({}) }}>
                Cancel
              </Button>
            )}
          </div>
        </div>
      )}

      {!connection && info.fields.length === 0 && (
        <Button size="sm" onClick={() => run(() => brokersApi.save(info.provider, {}))} disabled={busy}>
          Enable
        </Button>
      )}

      {connection && !editing && status !== 'connected' && info.login === 'redirect' && (
        <Button size="sm" onClick={startLogin} disabled={busy}>
          <LogIn className="w-3.5 h-3.5 mr-1" />
          Log in to {info.label}
        </Button>
      )}

      {connection && !editing && status !== 'connected' && info.login === 'totp' && (
        <div className="flex items-end gap-2">
          <div className="space-y-1">
            <Label htmlFor={`${info.provider}-totp`} className="text-[12px]">
              Authenticator code
            </Label>
            <Input
              id={`${info.provider}-totp`}
              inputMode="numeric"
              autoComplete="one-time-code"
              maxLength={6}
              className="w-32"
              value={totp}
              onChange={(e) => setTotp(e.target.value.replace(/\D/g, ''))}
            />
          </div>
          <Button size="sm" onClick={submitTotp} disabled={busy || totp.length !== 6}>
            <LogIn className="w-3.5 h-3.5 mr-1" />
            Log in
          </Button>
        </div>
      )}

      {error && <p className="text-[12px] text-destructive">{error}</p>}
    </div>
  )
}

/** Connect Indian brokers for market data. Read-only: nothing here can place orders. */
export default function BrokerConnections() {
  const [overview, setOverview] = useState<BrokerOverview | null>(null)
  const [loadError, setLoadError] = useState('')
  const [notice] = useState(() => readLoginResult(window.location.search))

  const load = useCallback(() => {
    brokersApi
      .list()
      .then((data) => {
        setOverview(data)
        setLoadError('')
      })
      .catch((e) => setLoadError(e instanceof Error ? e.message : 'Could not load broker connections.'))
  }, [])

  useEffect(() => {
    load()
  }, [load])

  return (
    <section className="space-y-3" data-testid="broker-connections">
      <div>
        <h2 className="text-[15px] font-semibold text-foreground">Broker connections (India)</h2>
        <p className="text-[12px] text-muted-foreground mt-0.5">
          Market data comes from your own broker account, using your own API keys. Connections are
          read-only: this app cannot place, change or cancel orders. Keys are stored encrypted and never
          shown again.
        </p>
      </div>

      {notice && (
        <p
          className={`text-[12px] ${notice.status === 'connected' ? 'text-emerald-600' : 'text-destructive'}`}
          role="status"
        >
          {notice.status === 'connected'
            ? `Logged in to ${notice.provider}.`
            : `Login to ${notice.provider} did not complete. Please try again.`}
        </p>
      )}

      {loadError && <p className="text-[12px] text-destructive">{loadError}</p>}

      {overview && !overview.vault_configured && (
        <div className="card p-3 flex gap-2 text-[12px] text-amber-600" role="alert">
          <AlertTriangle className="w-4 h-4 flex-shrink-0" />
          <span>
            Broker connections are disabled because CREDENTIALS_MASTER_KEY is not set on the server. Generate
            one with <code>python -m src.platform.security.credential_vault</code> and restart.
          </span>
        </div>
      )}

      {overview && overview.vault_configured && (
        <div className="grid gap-3">
          {overview.available.map((info) => (
            <ProviderCard
              key={info.provider}
              info={info}
              connection={overview.connections.find((c) => c.provider === info.provider)}
              onChanged={load}
            />
          ))}
        </div>
      )}
    </section>
  )
}
