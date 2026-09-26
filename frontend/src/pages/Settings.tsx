import { useState, useEffect, useRef } from 'react'
import { Check, Eye, EyeOff, Plus, Pencil, Trash2, Star, Send, Cpu, Play, Download, Upload, FileJson, BarChart3, User, Radar } from 'lucide-react'
import { fetchAPI, type AIService, type AIModel, type NotifyChannel } from '@candlewise/api'
import { useAvatar, saveAvatar, fileToAvatarDataUrl } from '@/hooks/use-avatar'
import PatSection from '@/components/PatSection'
import { Input } from '@candlewise/base-ui/components/ui/input'
import { Label } from '@candlewise/base-ui/components/ui/label'
import { Button } from '@candlewise/base-ui/components/ui/button'
import { Switch } from '@candlewise/base-ui/components/ui/switch'
import { Dialog, DialogContent, DialogHeader, DialogTitle, DialogDescription } from '@candlewise/base-ui/components/ui/dialog'
import { Select, SelectTrigger, SelectValue, SelectContent, SelectItem } from '@candlewise/base-ui/components/ui/select'
import { useToast } from '@candlewise/base-ui/components/ui/toast'
import { PRODUCT_NAME, UPSTREAM_NAME, UPSTREAM_URL } from '@/lib/brand'

interface Setting {
  key: string
  value: string
  description: string
}

interface TemplatePayload {
  version: number
  exported_at?: string
  settings?: Record<string, string>
  agents?: any[]
  stocks?: any[]
}

interface FeedbackStats {
  range_days: number
  total: number
  useful: number
  useless: number
  useful_rate: number
  by_day: Array<{ day: string; total: number; useful: number; useless: number; useful_rate: number }>
  by_agent: Array<{ agent_name: string; total: number; useful: number; useless: number; useful_rate: number }>
}

interface AgentsHealth {
  timezone: string
  summary: {
    next_24h_count: number
    recent_failed_count: number
  }
}

interface ServiceForm {
  name: string
  base_url: string
  api_key: string
}

interface ModelForm {
  name: string
  service_id: number | null
  model: string
}

interface ChannelForm {
  name: string
  type: string
  config: Record<string, string>
}

interface ChannelFieldDef {
  key: string
  label: string
  placeholder: string
  secret?: boolean
  required?: boolean
}

const CHANNEL_TYPE_FIELDS: Record<string, { label: string; fields: ChannelFieldDef[] }> = {
  telegram: {
    label: 'Telegram',
    fields: [
      { key: 'bot_token', label: 'Bot Token', placeholder: '123456:ABC-DEF...', secret: true, required: true },
      { key: 'chat_id', label: 'Chat ID', placeholder: '-100123456789', required: true },
      { key: 'proxy', label: 'Proxy', placeholder: 'http://192.168.1.1:7890 or socks5://...' },
    ],
  },
  discord: {
    label: 'Discord',
    fields: [
      { key: 'webhook_id', label: 'Webhook ID', placeholder: 'The ID in the webhook URL', required: true },
      { key: 'webhook_token', label: 'Webhook Token', placeholder: 'The token in the webhook URL', secret: true, required: true },
    ],
  },
  pushover: {
    label: 'Pushover',
    fields: [
      { key: 'user_key', label: 'User Key', placeholder: 'User key', required: true },
      { key: 'app_token', label: 'App Token', placeholder: 'Application token', secret: true, required: true },
    ],
  },
}

const emptyServiceForm: ServiceForm = { name: '', base_url: '', api_key: '' }
const emptyModelForm: ModelForm = { name: '', service_id: null, model: '' }
const emptyChannelForm: ChannelForm = { name: '', type: 'telegram', config: {} }

export default function SettingsPage() {
  const [settings, setSettings] = useState<Setting[]>([])
  const [services, setServices] = useState<AIService[]>([])
  const [channels, setChannels] = useState<NotifyChannel[]>([])
  const [version, setVersion] = useState<string>('')
  const [loading, setLoading] = useState(true)
  const [health, setHealth] = useState<AgentsHealth | null>(null)
  const [saving, setSaving] = useState<string | null>(null)
  const [saved, setSaved] = useState<string | null>(null)
  const [edited, setEdited] = useState<Record<string, string>>({})

  const [systemQuery, setSystemQuery] = useState('')

  // Service dialog
  const [serviceDialogOpen, setServiceDialogOpen] = useState(false)
  const [serviceForm, setServiceForm] = useState<ServiceForm>(emptyServiceForm)
  const [editServiceId, setEditServiceId] = useState<number | null>(null)
  const [serviceKeyVisible, setServiceKeyVisible] = useState(false)

  // Model dialog
  const [modelDialogOpen, setModelDialogOpen] = useState(false)
  const [modelForm, setModelForm] = useState<ModelForm>(emptyModelForm)
  const [editModelId, setEditModelId] = useState<number | null>(null)

  // Batch selection of discovered models
  const [batchOpen, setBatchOpen] = useState(false)
  const [batchServiceId, setBatchServiceId] = useState<number | null>(null)
  const [batchCandidates, setBatchCandidates] = useState<string[]>([])
  const [batchChecked, setBatchChecked] = useState<Set<string>>(new Set())
  const [batchDefault, setBatchDefault] = useState<string>('')
  const [submittingBatch, setSubmittingBatch] = useState(false)
  const [discoveringService, setDiscoveringService] = useState<number | null>(null)

  // Channel dialog
  const [channelDialogOpen, setChannelDialogOpen] = useState(false)
  const [channelForm, setChannelForm] = useState<ChannelForm>(emptyChannelForm)
  const [editChannelId, setEditChannelId] = useState<number | null>(null)
  const [channelKeyVisible, setChannelKeyVisible] = useState(false)
  const [testing, setTesting] = useState<number | null>(null)
  const [testingModel, setTestingModel] = useState<number | null>(null)

  // Avatar
  const avatar = useAvatar()
  const avatarFileRef = useRef<HTMLInputElement | null>(null)
  const [avatarSaving, setAvatarSaving] = useState(false)

  // Templates (config pack)
  const [importMode, setImportMode] = useState<'merge' | 'replace'>('merge')
  const [importing, setImporting] = useState(false)
  const [exporting, setExporting] = useState(false)

  // Feedback stats
  const [fbStats, setFbStats] = useState<FeedbackStats | null>(null)
  const [fbLoading, setFbLoading] = useState(false)

  const importFileRef = useRef<HTMLInputElement | null>(null)

  const { toast } = useToast()

  const builtinTemplates: Array<{ name: string; desc: string; payload: TemplatePayload }> = [
    {
      name: 'Conservative',
      desc: 'Low disturbance: stricter intraday triggers; quiet hours recommended',
      payload: {
        version: 1,
        settings: {
          notify_quiet_hours: '23:00-07:00',
          notify_retry_attempts: '2',
          notify_retry_backoff_seconds: '2',
        },
        agents: [
          { name: 'premarket_outlook', enabled: true, schedule: '30 8 * * 1-5', execution_mode: 'batch' },
          { name: 'daily_report', enabled: true, schedule: '30 15 * * 1-5', execution_mode: 'batch' },
          { name: 'intraday_monitor', enabled: true, schedule: '*/10 9-15 * * 1-5', execution_mode: 'single', config: { event_only: true, price_alert_threshold: 4.0, volume_alert_ratio: 2.5, throttle_minutes: 45 } },
        ],
      },
    },
    {
      name: 'Balanced',
      desc: 'Recommended default: balances coverage and disturbance',
      payload: {
        version: 1,
        settings: {
          notify_retry_attempts: '2',
          notify_retry_backoff_seconds: '2',
        },
        agents: [
          { name: 'premarket_outlook', enabled: true, schedule: '30 8 * * 1-5', execution_mode: 'batch' },
          { name: 'daily_report', enabled: true, schedule: '30 15 * * 1-5', execution_mode: 'batch' },
          { name: 'intraday_monitor', enabled: true, schedule: '*/5 9-15 * * 1-5', execution_mode: 'single', config: { event_only: true, price_alert_threshold: 3.0, volume_alert_ratio: 2.0, throttle_minutes: 30 } },
        ],
      },
    },
    {
      name: 'Aggressive',
      desc: 'Higher frequency: catches changes earlier; suits short-term watching',
      payload: {
        version: 1,
        settings: {
          notify_retry_attempts: '3',
          notify_retry_backoff_seconds: '1',
        },
        agents: [
          { name: 'premarket_outlook', enabled: true, schedule: '10 8 * * 1-5', execution_mode: 'batch' },
          { name: 'daily_report', enabled: true, schedule: '10 15 * * 1-5', execution_mode: 'batch' },
          { name: 'intraday_monitor', enabled: true, schedule: '*/3 9-15 * * 1-5', execution_mode: 'single', config: { event_only: true, price_alert_threshold: 2.0, volume_alert_ratio: 1.8, throttle_minutes: 20 } },
        ],
      },
    },
  ]

  const load = async () => {
    try {
      const [settingsData, servicesData, channelsData, versionData, healthData] = await Promise.all([
        fetchAPI<Setting[]>('/settings'),
        fetchAPI<AIService[]>('/providers/services'),
        fetchAPI<NotifyChannel[]>('/channels'),
        fetchAPI<{ version: string }>('/settings/version'),
        fetchAPI<AgentsHealth>('/agents/health'),
      ])
      setSettings(settingsData)
      setServices(servicesData)
      setChannels(channelsData)
      setVersion(versionData.version)
      setHealth(healthData)
    } catch (e) {
      console.error(e)
    } finally {
      setLoading(false)
    }
  }

  const downloadJson = (name: string, obj: any) => {
    try {
      const blob = new Blob([JSON.stringify(obj, null, 2)], { type: 'application/json' })
      const url = URL.createObjectURL(blob)
      const a = document.createElement('a')
      a.href = url
      a.download = name
      document.body.appendChild(a)
      a.click()
      a.remove()
      URL.revokeObjectURL(url)
    } catch {
      // ignore
    }
  }

  const exportTemplate = async () => {
    setExporting(true)
    try {
      const data = await fetchAPI<TemplatePayload>('/templates/export')
      const date = new Date().toISOString().slice(0, 10)
      downloadJson(`candlewise-config-${date}.json`, data)
      toast('Config pack exported', 'success')
    } catch (e) {
      toast(e instanceof Error ? e.message : 'Export failed', 'error')
    } finally {
      setExporting(false)
    }
  }

  const importTemplate = async (payload: TemplatePayload) => {
    setImporting(true)
    try {
      const resp = await fetchAPI<any>(`/templates/import?mode=${importMode}`, {
        method: 'POST',
        body: JSON.stringify(payload),
      })
      toast('Config pack imported', 'success')
      // refresh
      await load()
      return resp
    } catch (e) {
      toast(e instanceof Error ? e.message : 'Import failed', 'error')
      return null
    } finally {
      setImporting(false)
    }
  }

  const loadFeedbackStats = async () => {
    setFbLoading(true)
    try {
      const stats = await fetchAPI<FeedbackStats>('/feedback/stats?days=14')
      setFbStats(stats)
    } catch (e) {
      console.error(e)
      setFbStats(null)
    } finally {
      setFbLoading(false)
    }
  }

  useEffect(() => { load(); loadFeedbackStats() }, [])

  const onPickAvatar = async (e: React.ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0]
    e.target.value = '' // allow picking the same file again
    if (!file) return
    setAvatarSaving(true)
    try {
      const dataUrl = await fileToAvatarDataUrl(file)
      await saveAvatar(dataUrl)
      toast('Avatar updated', 'success')
    } catch (err) {
      toast(err instanceof Error ? err.message : 'Failed to save the avatar', 'error')
    } finally {
      setAvatarSaving(false)
    }
  }


  const handleSave = async (key: string) => {
    setSaving(key)
    try {
      await fetchAPI(`/settings/${key}`, {
        method: 'PUT',
        body: JSON.stringify({ value: edited[key] ?? settings.find(s => s.key === key)?.value }),
      })
      const newEdited = { ...edited }
      delete newEdited[key]
      setEdited(newEdited)
      setSaved(key)
      setTimeout(() => setSaved(null), 2000)
      load()
    } catch {
      toast('Save failed', 'error')
    } finally {
      setSaving(null)
    }
  }

  // Service CRUD
  const openServiceDialog = (svc?: AIService) => {
    if (svc) {
      setServiceForm({ name: svc.name, base_url: svc.base_url, api_key: svc.api_key })
      setEditServiceId(svc.id)
    } else {
      setServiceForm(emptyServiceForm)
      setEditServiceId(null)
    }
    setServiceKeyVisible(false)
    setServiceDialogOpen(true)
  }

  const saveService = async () => {
    try {
      let serviceId = editServiceId
      if (editServiceId) {
        await fetchAPI(`/providers/services/${editServiceId}`, { method: 'PUT', body: JSON.stringify(serviceForm) })
      } else {
        const created = await fetchAPI<AIService>('/providers/services', { method: 'POST', body: JSON.stringify(serviceForm) })
        serviceId = created.id
      }
      setServiceDialogOpen(false)
      await load()
      if (!editServiceId && serviceId) {
        try {
          const res = await fetchAPI<{ models: string[] }>(
            `/providers/services/${serviceId}/discover-models`,
            { method: 'POST' },
          )
          const found = res.models.filter(Boolean)
          if (found.length > 0) {
            setBatchServiceId(serviceId)
            setBatchCandidates(found)
            setBatchChecked(new Set())
            setBatchDefault('')
            setBatchOpen(true)
          } else {
            toast('Provider saved; no models were discovered, add them manually', 'info')
          }
        } catch (e) {
          toast(
            e instanceof Error
              ? `Provider saved; model discovery failed: ${e.message}. Add models manually`
              : "Provider saved; this provider doesn't support model discovery. Add models manually",
            'info',
          )
        }
      }
    } catch (e) {
      toast(e instanceof Error ? e.message : 'Save failed', 'error')
    }
  }

  // Discover models for a provider manually and open the batch selection (excluding models already added)
  const discoverForService = async (serviceId: number) => {
    setDiscoveringService(serviceId)
    try {
      const res = await fetchAPI<{ models: string[] }>(
        `/providers/services/${serviceId}/discover-models`,
        { method: 'POST' },
      )
      const svc = services.find(s => s.id === serviceId)
      const added = new Set((svc?.models || []).map(m => m.model))
      const found = res.models.filter(Boolean).filter(id => !added.has(id))
      if (found.length === 0) {
        toast('No new models found', 'info')
        return
      }
      setBatchServiceId(serviceId)
      setBatchCandidates(found)
      setBatchChecked(new Set())
      setBatchDefault('')
      setBatchOpen(true)
    } catch (e) {
      toast(e instanceof Error ? e.message : "This provider doesn't support model discovery", 'error')
    } finally {
      setDiscoveringService(null)
    }
  }

  const submitBatchModels = async () => {
    if (!batchServiceId) return
    const models = Array.from(batchChecked).map(m => ({
      name: '',
      model: m,
      is_default: m === batchDefault,
    }))
    if (models.length === 0) { setBatchOpen(false); return }
    setSubmittingBatch(true)
    try {
      await fetchAPI(`/providers/services/${batchServiceId}/models/batch`, {
        method: 'POST',
        body: JSON.stringify({ models }),
      })
      setBatchOpen(false)
      toast(`Added ${models.length} models`, 'success')
      load()
    } catch (e) {
      toast(e instanceof Error ? e.message : 'Batch add failed', 'error')
    } finally {
      setSubmittingBatch(false)
    }
  }

  const deleteService = async (id: number) => {
    if (!confirm('Deleting the provider also deletes all of its models. Continue?')) return
    try {
      await fetchAPI(`/providers/services/${id}`, { method: 'DELETE' })
      load()
    } catch (e) {
      toast(e instanceof Error ? e.message : 'Delete failed', 'error')
    }
  }

  // Model CRUD
  const openModelDialog = (serviceId?: number, model?: AIModel) => {
    if (model) {
      setModelForm({ name: model.name, service_id: model.service_id, model: model.model })
      setEditModelId(model.id)
    } else {
      setModelForm({ ...emptyModelForm, service_id: serviceId ?? null })
      setEditModelId(null)
    }
    setModelDialogOpen(true)
  }

  const saveModel = async () => {
    try {
      if (editModelId) {
        await fetchAPI(`/providers/models/${editModelId}`, { method: 'PUT', body: JSON.stringify(modelForm) })
      } else {
        await fetchAPI('/providers/models', { method: 'POST', body: JSON.stringify(modelForm) })
      }
      setModelDialogOpen(false)
      load()
    } catch (e) {
      toast(e instanceof Error ? e.message : 'Save failed', 'error')
    }
  }

  const deleteModel = async (id: number) => {
    if (!confirm('Delete this model?')) return
    try {
      await fetchAPI(`/providers/models/${id}`, { method: 'DELETE' })
      load()
    } catch (e) {
      toast(e instanceof Error ? e.message : 'Delete failed', 'error')
    }
  }

  const setDefaultModel = async (id: number) => {
    try {
      await fetchAPI(`/providers/models/${id}`, { method: 'PUT', body: JSON.stringify({ is_default: true }) })
      load()
    } catch {
      toast('Setting failed', 'error')
    }
  }

  const testModel = async (id: number) => {
    setTestingModel(id)
    try {
      await fetchAPI(`/providers/models/${id}/test`, { method: 'POST' })
      toast('Model test succeeded', 'success')
    } catch (e) {
      toast(e instanceof Error ? e.message : 'Test failed', 'error')
    } finally {
      setTestingModel(null)
    }
  }

  // Channel CRUD
  const openChannelDialog = (channel?: NotifyChannel) => {
    if (channel) {
      setChannelForm({
        name: channel.name,
        type: channel.type,
        config: channel.config ? { ...channel.config } : {},
      })
      setEditChannelId(channel.id)
    } else {
      setChannelForm(emptyChannelForm)
      setEditChannelId(null)
    }
    setChannelKeyVisible(false)
    setChannelDialogOpen(true)
  }

  const saveChannel = async () => {
    const payload = {
      name: channelForm.name,
      type: channelForm.type,
      config: channelForm.config,
    }
    try {
      if (editChannelId) {
        await fetchAPI(`/channels/${editChannelId}`, { method: 'PUT', body: JSON.stringify(payload) })
      } else {
        await fetchAPI('/channels', { method: 'POST', body: JSON.stringify(payload) })
      }
      setChannelDialogOpen(false)
      load()
    } catch (e) {
      toast(e instanceof Error ? e.message : 'Save failed', 'error')
    }
  }

  const isChannelFormValid = () => {
    if (!channelForm.name) return false
    const typeDef = CHANNEL_TYPE_FIELDS[channelForm.type]
    if (!typeDef) return false
    return typeDef.fields
      .filter(f => f.required)
      .every(f => !!channelForm.config[f.key]?.trim())
  }

  const deleteChannel = async (id: number) => {
    if (!confirm('Delete this notification channel?')) return
    try {
      await fetchAPI(`/channels/${id}`, { method: 'DELETE' })
      load()
    } catch (e) {
      toast(e instanceof Error ? e.message : 'Delete failed', 'error')
    }
  }

  const setDefaultChannel = async (id: number) => {
    try {
      await fetchAPI(`/channels/${id}`, { method: 'PUT', body: JSON.stringify({ is_default: true }) })
      load()
    } catch {
      toast('Setting failed', 'error')
    }
  }

  const toggleChannelEnabled = async (channel: NotifyChannel) => {
    try {
      await fetchAPI(`/channels/${channel.id}`, { method: 'PUT', body: JSON.stringify({ enabled: !channel.enabled }) })
      load()
    } catch {
      toast('Action failed', 'error')
    }
  }

  const testChannel = async (id: number) => {
    setTesting(id)
    try {
      await fetchAPI(`/channels/${id}/test`, { method: 'POST' })
      toast('Test notification sent', 'success')
    } catch (e) {
      toast(e instanceof Error ? e.message : 'Test failed', 'error')
    } finally {
      setTesting(null)
    }
  }

  if (loading) {
    return (
      <div className="flex items-center justify-center py-20">
        <span className="w-5 h-5 border-2 border-primary/30 border-t-primary rounded-full animate-spin" />
      </div>
    )
  }

  const allModels = services.flatMap(s => s.models || [])
  const defaultModel = allModels.find(m => m.is_default)
  const defaultChannel = channels.find(c => c.is_default)
  const enabledChannels = channels.filter(c => c.enabled)

  const filteredSettings = settings.filter(s => {
    const q = systemQuery.trim().toLowerCase()
    if (!q) return true
    return (s.description || '').toLowerCase().includes(q) || (s.key || '').toLowerCase().includes(q)
  })

  // Ordered by importance: common first, rare last
  const jumpItems: Array<{ id: string; label: string; hint?: string }> = [
    { id: 'sec-ai', label: 'AI', hint: `${services.length} providers / ${allModels.length} models` },
    { id: 'sec-notify', label: 'Notifications', hint: `${enabledChannels.length}/${channels.length} on` },
    { id: 'sec-system', label: 'System', hint: health?.timezone ? `TZ ${health.timezone}` : undefined },
    { id: 'sec-pack', label: 'Config pack' },
    { id: 'sec-feedback', label: 'Feedback' },
    { id: 'sec-pat', label: 'MCP tokens' },
  ]

  const scrollTo = (id: string) => {
    const el = document.getElementById(id)
    if (!el) return
    el.scrollIntoView({ behavior: 'smooth', block: 'start' })
  }

  return (
    <div>
      {/* Hero */}
      <div className="card relative overflow-hidden p-5 md:p-7">
        <div className="pointer-events-none absolute inset-0 bg-gradient-to-br from-primary/10 via-transparent to-accent/30" />
        <div className="relative flex flex-col md:flex-row md:items-end md:justify-between gap-4">
          <div className="min-w-0">
            <div className="flex flex-wrap items-center gap-2 text-[11px] text-muted-foreground">
              <input ref={avatarFileRef} type="file" accept="image/*" className="hidden" onChange={onPickAvatar} />
              <button
                type="button"
                onClick={() => avatarFileRef.current?.click()}
                disabled={avatarSaving}
                title="Click to upload an avatar"
                className="group relative h-9 w-9 rounded-full overflow-hidden bg-gradient-to-br from-primary to-primary/70 text-white shadow-sm flex items-center justify-center ring-1 ring-border/40 hover:ring-primary/40 transition-all shrink-0"
              >
                {avatar ? (
                  <img src={avatar} alt="Avatar" className="w-full h-full object-cover" />
                ) : (
                  <User className="w-4 h-4" />
                )}
                <span className="absolute inset-0 flex items-center justify-center bg-black/40 opacity-0 group-hover:opacity-100 transition-opacity">
                  <Upload className="w-3.5 h-3.5 text-white" />
                </span>
              </button>
              <span className="mx-1 hidden h-4 w-px bg-border/50 sm:block" />
              <div className="px-2.5 py-1 rounded-full bg-background/70 border border-border/50 text-[11px] text-muted-foreground">
                <span className="font-mono text-foreground/90">{services.length}</span> providers
              </div>
              <div className="px-2.5 py-1 rounded-full bg-background/70 border border-border/50 text-[11px] text-muted-foreground">
                <span className="font-mono text-foreground/90">{allModels.length}</span> models
              </div>
              <div className="px-2.5 py-1 rounded-full bg-background/70 border border-border/50 text-[11px] text-muted-foreground">
                <span className="font-mono text-foreground/90">{enabledChannels.length}</span>/<span className="font-mono">{channels.length}</span> channels on
              </div>
              {defaultModel ? (
                <div className="px-2.5 py-1 rounded-full bg-background/70 border border-border/50 text-[11px] text-muted-foreground">
                  Default model <span className="font-mono text-foreground/90">{defaultModel.model}</span>
                </div>
              ) : null}
              {defaultChannel ? (
                <div className="px-2.5 py-1 rounded-full bg-background/70 border border-border/50 text-[11px] text-muted-foreground">
                  Default channel <span className="text-foreground/90">{defaultChannel.name}</span>
                </div>
              ) : null}
            </div>
          </div>

          <div className="flex flex-col sm:flex-row gap-2">
            <Button variant="secondary" size="sm" className="h-9" onClick={exportTemplate} disabled={exporting}>
              <Download className="w-3.5 h-3.5" /> Export config pack
            </Button>
            <Button size="sm" className="h-9" onClick={() => scrollTo('sec-ai')}>
              <Cpu className="w-3.5 h-3.5" /> Set up AI
            </Button>
          </div>
        </div>

        {/* Jump pills */}
        <div className="relative mt-4 flex flex-wrap gap-2">
          {jumpItems.map(it => (
            <button
              key={it.id}
              onClick={() => scrollTo(it.id)}
              className="group flex items-center gap-2 rounded-full border border-border/50 bg-background/70 px-3 py-1.5 text-[11px] text-muted-foreground hover:text-foreground hover:border-primary/30 transition-colors"
            >
              <span className="font-medium text-foreground/90 group-hover:text-foreground">{it.label}</span>
              {it.hint ? <span className="opacity-60">{it.hint}</span> : null}
            </button>
          ))}
        </div>
      </div>

      <div className="mt-6 grid grid-cols-1 lg:grid-cols-12 gap-6">
        {/* AI Services + Models Section */}
        <section id="sec-ai" className="card p-4 md:p-6 lg:col-span-7">
          <div className="flex items-start justify-between mb-4 md:mb-5 gap-3">
            <div>
              <h3 className="text-[12px] md:text-[13px] font-semibold text-foreground">AI providers &amp; models</h3>
              <p className="text-[11px] text-muted-foreground mt-1">Connect your AI service and choose a default model</p>
            </div>
            <Button size="sm" className="h-8" onClick={() => openServiceDialog()}>
              <Plus className="w-3.5 h-3.5" />
              <span className="hidden sm:inline">Add provider</span>
            </Button>
          </div>
          {services.length === 0 ? (
            <p className="text-[13px] text-muted-foreground text-center py-6">No AI providers yet; click "Add provider" to create one</p>
          ) : (
            <div className="space-y-4">
              {services.map(svc => (
                <div key={svc.id} className="rounded-xl bg-accent/30 overflow-hidden">
                  {/* Service header */}
                  <div className="flex items-center justify-between p-3.5">
                    <div className="min-w-0">
                      <span className="text-[13px] font-medium text-foreground">{svc.name}</span>
                      <p className="text-[11px] text-muted-foreground mt-0.5 truncate font-mono">{svc.base_url}</p>
                    </div>
                    <div className="flex items-center gap-1 flex-shrink-0">
                      <Button size="sm" variant="ghost" className="h-7 text-[11px]" onClick={() => openModelDialog(svc.id)}>
                        <Plus className="w-3 h-3" /> Model
                      </Button>
                      <Button
                        variant="ghost" size="icon" className="h-7 w-7"
                        title="Discover models (find available models automatically)"
                        disabled={discoveringService === svc.id}
                        onClick={() => discoverForService(svc.id)}
                      >
                        <Radar className={`w-3.5 h-3.5 ${discoveringService === svc.id ? 'animate-pulse' : ''}`} />
                      </Button>
                      <Button variant="ghost" size="icon" className="h-7 w-7" onClick={() => openServiceDialog(svc)}>
                        <Pencil className="w-3.5 h-3.5" />
                      </Button>
                      <Button variant="ghost" size="icon" className="h-7 w-7 hover:text-destructive" onClick={() => deleteService(svc.id)}>
                        <Trash2 className="w-3.5 h-3.5" />
                      </Button>
                    </div>
                  </div>
                  {/* Models under this service */}
                  {svc.models.length > 0 && (
                    <div className="px-3.5 pb-3.5 space-y-1.5">
                      {svc.models.map(m => (
                        <div key={m.id} className="flex items-center justify-between px-3 py-2 rounded-lg bg-background/60">
                          <div className="flex items-center gap-2">
                            {m.is_default && <Star className="w-3 h-3 text-amber-500" />}
                            <Cpu className="w-3 h-3 text-muted-foreground" />
                            <span className="text-[12px] font-medium text-foreground">{m.name}</span>
                            <span className="text-[11px] text-muted-foreground font-mono">{m.model}</span>
                          </div>
                          <div className="flex items-center gap-0.5">
                            <Button
                              variant="ghost" size="icon" className="h-6 w-6"
                              onClick={() => testModel(m.id)}
                              disabled={testingModel === m.id}
                              title="Test model"
                            >
                              {testingModel === m.id ? (
                                <span className="w-3 h-3 border-2 border-current/30 border-t-current rounded-full animate-spin" />
                              ) : (
                                <Play className="w-3 h-3" />
                              )}
                            </Button>
                            {!m.is_default && (
                              <Button variant="ghost" size="icon" className="h-6 w-6" onClick={() => setDefaultModel(m.id)} title="Set as default">
                                <Star className="w-3 h-3" />
                              </Button>
                            )}
                            <Button variant="ghost" size="icon" className="h-6 w-6" onClick={() => openModelDialog(svc.id, m)}>
                              <Pencil className="w-3 h-3" />
                            </Button>
                            <Button variant="ghost" size="icon" className="h-6 w-6 hover:text-destructive" onClick={() => deleteModel(m.id)}>
                              <Trash2 className="w-3 h-3" />
                            </Button>
                          </div>
                        </div>
                      ))}
                    </div>
                  )}
                </div>
              ))}
            </div>
          )}
        </section>

        {/* Notify Channel Section */}
        <section id="sec-notify" className="card p-4 md:p-6 lg:col-span-5">
          <div className="flex items-start justify-between mb-4 md:mb-5 gap-3">
            <div>
              <h3 className="text-[12px] md:text-[13px] font-semibold text-foreground">Notification channels</h3>
              <p className="text-[11px] text-muted-foreground mt-1">Send to Telegram, Discord or Pushover</p>
            </div>
            <Button size="sm" className="h-8" onClick={() => openChannelDialog()}>
              <Plus className="w-3.5 h-3.5" />
              <span className="hidden sm:inline">Add</span>
            </Button>
          </div>
          {channels.length === 0 ? (
            <p className="text-[13px] text-muted-foreground text-center py-6">No notification channels yet; click "Add" to create one</p>
          ) : (
            <div className="space-y-3">
              {channels.map(ch => (
                <div key={ch.id} className="flex items-center justify-between p-3.5 rounded-xl bg-accent/30 hover:bg-accent/50 transition-colors">
                  <div className="flex items-center gap-3 min-w-0">
                    {ch.is_default && <Star className="w-3.5 h-3.5 text-amber-500 flex-shrink-0" />}
                    <div className="min-w-0">
                      <span className="text-[13px] font-medium text-foreground">{ch.name}</span>
                      <p className="text-[11px] text-muted-foreground mt-0.5">{CHANNEL_TYPE_FIELDS[ch.type]?.label || ch.type}</p>
                    </div>
                  </div>
                  <div className="flex items-center gap-1 flex-shrink-0">
                    <Button
                      variant="ghost" size="icon" className="h-7 w-7"
                      onClick={() => testChannel(ch.id)}
                      disabled={testing === ch.id || !ch.enabled}
                      title="Send test"
                    >
                      {testing === ch.id ? (
                        <span className="w-3 h-3 border-2 border-current/30 border-t-current rounded-full animate-spin" />
                      ) : (
                        <Send className="w-3.5 h-3.5" />
                      )}
                    </Button>
                    {!ch.is_default && (
                      <Button variant="ghost" size="icon" className="h-7 w-7" onClick={() => setDefaultChannel(ch.id)} title="Set as default">
                        <Star className="w-3.5 h-3.5" />
                      </Button>
                    )}
                    <Switch checked={ch.enabled} onCheckedChange={() => toggleChannelEnabled(ch)} />
                    <Button variant="ghost" size="icon" className="h-7 w-7" onClick={() => openChannelDialog(ch)}>
                      <Pencil className="w-3.5 h-3.5" />
                    </Button>
                    <Button variant="ghost" size="icon" className="h-7 w-7 hover:text-destructive" onClick={() => deleteChannel(ch.id)}>
                      <Trash2 className="w-3.5 h-3.5" />
                    </Button>
                  </div>
                </div>
              ))}
            </div>
          )}
        </section>

        {/* General Settings */}
        {settings.length > 0 && (
          <section id="sec-system" className="card p-4 md:p-6 lg:col-span-12">
            <div className="flex flex-col md:flex-row md:items-end md:justify-between gap-3 mb-4 md:mb-5">
              <div>
                <h3 className="text-[12px] md:text-[13px] font-semibold text-foreground">System</h3>
                <p className="text-[11px] text-muted-foreground mt-1">Preferences and advanced options. Changes take effect at once.</p>
              </div>
              <div className="flex items-center gap-2">
                <Input
                  value={systemQuery}
                  onChange={e => setSystemQuery(e.target.value)}
                  placeholder="Search settings (description / key)"
                  className="h-9 w-full md:w-[320px]"
                />
                {health?.timezone ? (
                  <div className="hidden md:flex px-2.5 h-9 items-center rounded-lg border border-border/50 bg-accent/20 text-[11px] text-muted-foreground">
                    TZ <span className="ml-1 font-mono text-foreground/90">{health.timezone}</span>
                  </div>
                ) : null}
              </div>
            </div>

            <div className="space-y-5">
              {filteredSettings.map(setting => {
                const currentValue = edited[setting.key] ?? setting.value
                const isChanged = setting.key in edited
                const STOCK_LINK_OPTIONS: Record<string, string> = { nse: 'NSE India', tradingview: 'TradingView', google: 'Google Finance' }
                return (
                  <div key={setting.key}>
                    <Label>{setting.description || setting.key}</Label>
                    <div className="flex items-center gap-2.5">
                      {setting.key === 'stock_link_platform' ? (
                        <Select
                          value={currentValue || 'nse'}
                          onValueChange={v => setEdited({ ...edited, [setting.key]: v })}
                        >
                          <SelectTrigger className={`${isChanged ? 'ring-2 ring-primary/20 border-primary/30' : ''}`}>
                            <SelectValue />
                          </SelectTrigger>
                          <SelectContent>
                            {Object.entries(STOCK_LINK_OPTIONS).map(([val, label]) => (
                              <SelectItem key={val} value={val}>{label}</SelectItem>
                            ))}
                          </SelectContent>
                        </Select>
                      ) : (
                      <Input
                        value={currentValue}
                        onChange={e => setEdited({ ...edited, [setting.key]: e.target.value })}
                        className={`font-mono ${isChanged ? 'ring-2 ring-primary/20 border-primary/30' : ''}`}
                        placeholder={setting.key}
                      />
                      )}
                      <button
                        onClick={() => handleSave(setting.key)}
                        disabled={!isChanged || saving === setting.key}
                        className={`w-10 h-10 rounded-lg flex items-center justify-center transition-all ${
                          saved === setting.key
                            ? 'bg-success/10 text-success'
                            : isChanged
                              ? 'bg-primary text-primary-foreground'
                              : 'text-muted-foreground/30'
                        }`}
                      >
                        {saving === setting.key ? (
                          <span className="w-4 h-4 border-2 border-current/30 border-t-current rounded-full animate-spin" />
                        ) : (
                          <Check className="w-4 h-4" />
                        )}
                      </button>
                    </div>
                  </div>
                )
              })}
            </div>
          </section>
        )}

        {/* Config Pack (Templates) */}
        <section id="sec-pack" className="card p-4 md:p-6 lg:col-span-7">
          <div className="flex items-start justify-between mb-4 gap-3">
            <div>
              <h3 className="text-[12px] md:text-[13px] font-semibold text-foreground">Config pack</h3>
              <p className="text-[11px] text-muted-foreground mt-1">Import/export agents, the watchlist and system settings in one go</p>
            </div>
            <div className="flex items-center gap-2">
              <Button variant="secondary" size="sm" className="h-8" onClick={exportTemplate} disabled={exporting}>
                <Download className="w-3.5 h-3.5" />
                <span className="hidden sm:inline">Export</span>
              </Button>
              <Button
                variant="secondary"
                size="sm"
                className="h-8"
                onClick={() => importFileRef.current?.click()}
                disabled={importing}
              >
                <Upload className="w-3.5 h-3.5" />
                <span className="hidden sm:inline">Import</span>
              </Button>
            </div>
          </div>

          <div className="flex items-center gap-2 mb-4">
            <div className="text-[11px] text-muted-foreground">Import mode</div>
            <Select value={importMode} onValueChange={(v) => setImportMode(v as any)}>
              <SelectTrigger className="h-8 w-[160px] text-[12px]">
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value="merge">Merge updates (recommended)</SelectItem>
                <SelectItem value="replace">Replace (only what the pack contains)</SelectItem>
              </SelectContent>
            </Select>
          </div>

          <input
            ref={importFileRef}
            type="file"
            accept="application/json"
            className="hidden"
            onChange={async (e) => {
              const file = e.target.files?.[0]
              e.target.value = ''
              if (!file) return
              try {
                const text = await file.text()
                const payload = JSON.parse(text)
                await importTemplate(payload)
              } catch (err) {
                toast('Could not parse the config pack', 'error')
              }
            }}
          />

          <div className="rounded-xl border border-border/40 bg-accent/20 p-3">
            <div className="flex items-center gap-2 text-[12px] font-semibold text-foreground">
              <FileJson className="w-4 h-4 text-muted-foreground" />
              Built-in templates
            </div>
            <div className="mt-2 grid grid-cols-1 md:grid-cols-3 gap-2">
              {builtinTemplates.map(t => (
                <div key={t.name} className="rounded-lg border border-border/40 bg-background/30 p-3">
                  <div className="flex items-center justify-between">
                    <div className="text-[12px] font-semibold text-foreground">{t.name}</div>
                    <Button
                      size="sm"
                      className="h-7"
                      onClick={() => importTemplate(t.payload)}
                      disabled={importing}
                    >
                      <span className="text-[12px]">Apply</span>
                    </Button>
                  </div>
                  <div className="mt-1 text-[11px] text-muted-foreground">{t.desc}</div>
                </div>
              ))}
            </div>
          </div>
        </section>

        {/* Feedback Stats */}
        <section id="sec-feedback" className="card p-4 md:p-6 lg:col-span-5">
          <div className="flex items-center justify-between mb-4">
            <div>
              <h3 className="text-[12px] md:text-[13px] font-semibold text-foreground">Feedback on items</h3>
              <p className="text-[11px] text-muted-foreground mt-1">Used to judge notification quality and improve strategies</p>
            </div>
            <Button variant="secondary" size="sm" className="h-8" onClick={loadFeedbackStats} disabled={fbLoading}>
              <BarChart3 className="w-3.5 h-3.5" />
              <span className="hidden sm:inline">Refresh</span>
            </Button>
          </div>

          {fbStats ? (
            <div className="space-y-3">
              <div className="flex flex-wrap items-center gap-2 text-[12px] text-muted-foreground">
                <span>Last {fbStats.range_days} days</span>
                <span className="opacity-50">|</span>
                <span>Feedback: <span className="font-mono text-foreground/90">{fbStats.total}</span></span>
                <span className="opacity-50">|</span>
                <span>Useful: <span className="font-mono text-success">{fbStats.useful}</span></span>
                <span className="opacity-50">|</span>
                <span>Not useful: <span className="font-mono text-destructive">{fbStats.useless}</span></span>
                <span className="opacity-50">|</span>
                <span>Useful rate: <span className="font-mono text-foreground/90">{Math.round(fbStats.useful_rate * 100)}%</span></span>
              </div>

              {fbStats.by_agent?.length ? (
                <div className="rounded-xl border border-border/40 bg-accent/20 p-3">
                  <div className="text-[12px] font-semibold text-foreground">By agent</div>
                  <div className="mt-2 space-y-1">
                    {fbStats.by_agent.slice(0, 6).map(a => (
                      <div key={a.agent_name} className="flex items-center justify-between text-[11px]">
                        <span className="font-mono text-muted-foreground">{a.agent_name}</span>
                        <span className="font-mono text-muted-foreground">
                          {a.useful}/{a.total} ({Math.round(a.useful_rate * 100)}%)
                        </span>
                      </div>
                    ))}
                  </div>
                </div>
              ) : (
                <div className="text-[12px] text-muted-foreground">No feedback yet</div>
              )}
            </div>
          ) : (
            <div className="text-[12px] text-muted-foreground">No feedback yet</div>
          )}
        </section>

        {/* MCP access tokens */}
        <PatSection />

      </div>

      {/* Service Dialog */}
      <Dialog open={serviceDialogOpen} onOpenChange={setServiceDialogOpen}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>{editServiceId ? 'Edit AI provider' : 'Add AI provider'}</DialogTitle>
            <DialogDescription>Set up the AI provider's API connection</DialogDescription>
          </DialogHeader>
          <div className="space-y-4 mt-2">
            <div>
              <Label>Name</Label>
              <Input
                value={serviceForm.name}
                onChange={e => setServiceForm({ ...serviceForm, name: e.target.value })}
                placeholder="e.g. OpenAI, Anthropic, DeepSeek"
              />
            </div>
            <div>
              <Label>Base URL</Label>
              <Input
                value={serviceForm.base_url}
                onChange={e => setServiceForm({ ...serviceForm, base_url: e.target.value })}
                placeholder="https://api.openai.com/v1"
                className="font-mono"
              />
            </div>
            <div>
              <Label>API Key</Label>
              <div className="relative">
                <Input
                  type={serviceKeyVisible ? 'text' : 'password'}
                  value={serviceForm.api_key}
                  onChange={e => setServiceForm({ ...serviceForm, api_key: e.target.value })}
                  placeholder="sk-..."
                  className="font-mono pr-10"
                />
                <Button
                  type="button" variant="ghost" size="icon"
                  className="absolute right-1 top-1/2 -translate-y-1/2 h-8 w-8"
                  onClick={() => setServiceKeyVisible(!serviceKeyVisible)}
                >
                  {serviceKeyVisible ? <EyeOff className="w-4 h-4" /> : <Eye className="w-4 h-4" />}
                </Button>
              </div>
            </div>
            <div className="flex justify-end gap-2 pt-2">
              <Button variant="ghost" onClick={() => setServiceDialogOpen(false)}>Cancel</Button>
              <Button onClick={saveService} disabled={!serviceForm.name || !serviceForm.base_url}>
                {editServiceId ? 'Save' : 'Create'}
              </Button>
            </div>
          </div>
        </DialogContent>
      </Dialog>

      {/* Model Dialog */}
      <Dialog open={modelDialogOpen} onOpenChange={setModelDialogOpen}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>{editModelId ? 'Edit model' : 'Add model'}</DialogTitle>
            <DialogDescription>Set up an AI model</DialogDescription>
          </DialogHeader>
          <div className="space-y-4 mt-2">
            <div>
              <Label>Provider</Label>
              <Select
                value={modelForm.service_id?.toString() ?? ''}
                onValueChange={val => setModelForm({ ...modelForm, service_id: val ? parseInt(val) : null })}
              >
                <SelectTrigger>
                  <SelectValue placeholder="Choose a provider" />
                </SelectTrigger>
                <SelectContent>
                  {services.map(s => (
                    <SelectItem key={s.id} value={s.id.toString()}>{s.name}</SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </div>
            <div>
              <Label>Display name <span className="text-muted-foreground font-normal">(optional; defaults to the model id)</span></Label>
              <Input
                value={modelForm.name}
                onChange={e => setModelForm({ ...modelForm, name: e.target.value })}
                placeholder="Leave empty to use the model id"
              />
            </div>
            <div>
              <Label>Model id <span className="text-muted-foreground font-normal">(use "Discover" on the provider to find them in bulk)</span></Label>
              <Input
                value={modelForm.model}
                disabled={!modelForm.service_id}
                onChange={e => setModelForm({ ...modelForm, model: e.target.value })}
                placeholder={modelForm.service_id ? 'gpt-4o / deepseek-chat' : 'Choose a provider first'}
                className="font-mono"
              />
            </div>
            <div className="flex justify-end gap-2 pt-2">
              <Button variant="ghost" onClick={() => setModelDialogOpen(false)}>Cancel</Button>
              <Button onClick={saveModel} disabled={!modelForm.model || !modelForm.service_id}>
                {editModelId ? 'Save' : 'Create'}
              </Button>
            </div>
          </div>
        </DialogContent>
      </Dialog>

      {/* Batch selection of discovered models */}
      <Dialog open={batchOpen} onOpenChange={setBatchOpen}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>Found {batchCandidates.length} models</DialogTitle>
            <DialogDescription>Tick the models to add; you can mark one as the default</DialogDescription>
          </DialogHeader>
          <div className="mt-3 flex items-center justify-between px-0.5 text-xs text-muted-foreground">
            <span>Selected <span className="font-mono text-foreground">{batchChecked.size}</span> / {batchCandidates.length}</span>
            <button
              type="button"
              className="hover:text-foreground"
              onClick={() => setBatchChecked(
                batchChecked.size === batchCandidates.length ? new Set() : new Set(batchCandidates),
              )}
            >
              {batchChecked.size === batchCandidates.length ? 'Deselect all' : 'Select all'}
            </button>
          </div>
          <div className="mt-1.5 max-h-80 space-y-1.5 overflow-y-auto scrollbar pr-1">
            {batchCandidates.map(id => {
              const checked = batchChecked.has(id)
              const isDefault = batchDefault === id
              return (
                <div
                  key={id}
                  onClick={() => {
                    const next = new Set(batchChecked)
                    if (checked) { next.delete(id); if (isDefault) setBatchDefault('') }
                    else next.add(id)
                    setBatchChecked(next)
                  }}
                  className={`flex cursor-pointer items-center justify-between gap-3 rounded-lg border px-3 py-2.5 transition-colors ${
                    checked ? 'border-primary/60 bg-primary/10' : 'border-border/50 hover:border-border hover:bg-muted/40'
                  }`}
                >
                  <div className="flex min-w-0 items-center gap-2.5">
                    <span className={`flex h-4 w-4 shrink-0 items-center justify-center rounded border ${
                      checked ? 'border-primary bg-primary text-primary-foreground' : 'border-muted-foreground/40'
                    }`}>
                      {checked && <Check className="h-3 w-3" strokeWidth={3} />}
                    </span>
                    <span className="truncate font-mono text-sm">{id}</span>
                  </div>
                  <button
                    type="button"
                    onClick={e => {
                      e.stopPropagation()
                      if (isDefault) { setBatchDefault('') }
                      else {
                        setBatchDefault(id)
                        if (!checked) { const next = new Set(batchChecked); next.add(id); setBatchChecked(next) }
                      }
                    }}
                    className={`inline-flex shrink-0 items-center gap-1 rounded-full px-2 py-0.5 text-[11px] transition-colors ${
                      isDefault ? 'bg-primary text-primary-foreground' : 'text-muted-foreground hover:bg-muted hover:text-foreground'
                    }`}
                  >
                    <Star className={`h-3 w-3 ${isDefault ? 'fill-current' : ''}`} />
                    {isDefault ? 'Default' : 'Make default'}
                  </button>
                </div>
              )
            })}
          </div>
          <div className="flex justify-end gap-2 pt-2">
            <Button variant="ghost" onClick={() => setBatchOpen(false)}>Skip</Button>
            <Button onClick={submitBatchModels} disabled={batchChecked.size === 0 || submittingBatch}>
              {submittingBatch ? 'Adding…' : `Add ${batchChecked.size}`}
            </Button>
          </div>
        </DialogContent>
      </Dialog>

      {/* Channel Dialog */}
      <Dialog open={channelDialogOpen} onOpenChange={setChannelDialogOpen}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>{editChannelId ? 'Edit notification channel' : 'Add notification channel'}</DialogTitle>
            <DialogDescription>Set up how notifications are sent</DialogDescription>
          </DialogHeader>
          <div className="space-y-4 mt-2">
            <div>
              <Label>Name</Label>
              <Input
                value={channelForm.name}
                onChange={e => setChannelForm({ ...channelForm, name: e.target.value })}
                placeholder="e.g. My Telegram"
              />
            </div>
            <div>
              <Label>Type</Label>
              <Select
                value={channelForm.type}
                onValueChange={val => setChannelForm({ ...channelForm, type: val, config: {} })}
              >
                <SelectTrigger>
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  {Object.entries(CHANNEL_TYPE_FIELDS).map(([key, def]) => (
                    <SelectItem key={key} value={key}>{def.label}</SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </div>
            {CHANNEL_TYPE_FIELDS[channelForm.type]?.fields.map(field => (
              <div key={field.key}>
                <Label>{field.label}{!field.required && <span className="text-muted-foreground font-normal"> (optional)</span>}</Label>
                <div className="relative">
                  <Input
                    type={field.secret && !channelKeyVisible ? 'password' : 'text'}
                    value={channelForm.config[field.key] || ''}
                    onChange={e => setChannelForm({
                      ...channelForm,
                      config: { ...channelForm.config, [field.key]: e.target.value },
                    })}
                    placeholder={field.placeholder}
                    className={`font-mono ${field.secret ? 'pr-10' : ''}`}
                  />
                  {field.secret && (
                    <Button
                      type="button" variant="ghost" size="icon"
                      className="absolute right-1 top-1/2 -translate-y-1/2 h-8 w-8"
                      onClick={() => setChannelKeyVisible(!channelKeyVisible)}
                    >
                      {channelKeyVisible ? <EyeOff className="w-4 h-4" /> : <Eye className="w-4 h-4" />}
                    </Button>
                  )}
                </div>
              </div>
            ))}
            <div className="flex justify-end gap-2 pt-2">
              <Button variant="ghost" onClick={() => setChannelDialogOpen(false)}>Cancel</Button>
              <Button onClick={saveChannel} disabled={!isChannelFormValid()}>
                {editChannelId ? 'Save' : 'Create'}
              </Button>
            </div>
          </div>
        </DialogContent>
      </Dialog>

      {/* Version Footer */}
      <div className="mt-8 text-center text-[11px] text-muted-foreground/60 space-y-0.5">
        {version && <div>{PRODUCT_NAME} v{version}</div>}
        <div>
          Based on{' '}
          <a href={UPSTREAM_URL} target="_blank" rel="noopener noreferrer" className="underline hover:text-foreground">
            {UPSTREAM_NAME}
          </a>{' '}
          by TNT-Likely (MIT)
        </div>
      </div>
    </div>
  )
}
