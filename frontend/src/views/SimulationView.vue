<script setup lang="ts">
import { ref, computed, onMounted, onUnmounted, watch } from 'vue'
import { useWebSocket } from '@/composables/useWebSocket'
import { useHomeBuilderStore } from '@/stores/homeBuilder'
import { useThreatBuilderStore } from '@/stores/threatBuilder'
import { useSessionStore } from '@/stores/session'
import * as api from '@/services/apiClient'

const homeStore = useHomeBuilderStore()
const threatStore = useThreatBuilderStore()
const sessionStore = useSessionStore()

const isRealOnly = computed(() => sessionStore.deviceMode === 'real')

// ── Types ──
interface SimRoom {
  id: string; name: string; type: string
  x: number; y: number; width: number; height: number
  devices: SimDevice[]
}
interface SimDevice {
  id: string; name: string; type: string; icon: string
  status: 'normal' | 'warning' | 'compromised' | 'offline'
}
interface SimThreat {
  id: string; name: string; type: string
  startTime: number; duration: number
  severity: 'low' | 'medium' | 'high' | 'critical'
  targetDevice: string; description: string
  status: 'pending' | 'active' | 'detected' | 'blocked' | 'completed'
}
interface SimEvent {
  id: string; timestamp: string
  type: 'info' | 'warning' | 'attack' | 'detection' | 'system'
  message: string; deviceId?: string; severity?: string
}

// ── Room Type Config ──
const roomTypeConfig: Record<string, { icon: string; color: string }> = {
  living_room: { icon: '🛋️', color: '#3b82f6' },
  bedroom: { icon: '🛏️', color: '#8b5cf6' },
  kitchen: { icon: '🍳', color: '#f59e0b' },
  bathroom: { icon: '🚿', color: '#06b6d4' },
  office: { icon: '💻', color: '#10b981' },
  garage: { icon: '🚗', color: '#6b7280' },
  hallway: { icon: '🚶', color: '#78716c' },
  dining_room: { icon: '🍽️', color: '#ec4899' },
  laundry: { icon: '👕', color: '#14b8a6' },
  storage: { icon: '📦', color: '#a3a3a3' },
}

const deviceIconMap: Record<string, string> = {
  smart_thermostat: '🌡️', smart_lock: '🔒', smart_camera: '📷', smart_light: '💡',
  smart_speaker: '🔊', motion_sensor: '👁️', door_sensor: '🚪', window_sensor: '🪟',
  smoke_detector: '🔥', co_detector: '☁️', water_leak: '💧', smart_plug: '🔌',
  smart_tv: '📺', smart_fridge: '🧊', smart_oven: '♨️', smart_washer: '🫧',
  robot_vacuum: '🤖', smart_sprinkler: '🌊', smart_blinds: '🪟', smart_fan: '🌀',
  air_purifier: '🌬️', smart_meter: '⚡', smart_doorbell: '🔔', baby_monitor: '👶',
  pet_feeder: '🐾', security_camera: '📷', thermostat: '🌡️', camera: '📷',
  router: '📶', default: '📱',
}

function getDeviceIcon(type: string): string {
  return deviceIconMap[type] || deviceIconMap.default
}

// ── State ──
const rooms = ref<SimRoom[]>([])
const threats = ref<SimThreat[]>([])
const simStatus = ref<any>(null)
const eventLog = ref<SimEvent[]>([])
const loading = ref(false)
const error = ref('')

// Config
const duration = ref(24)
const timeCompression = ref(60)
const includeThreats = ref(true)
const simulationSpeed = ref(1)
const simMode = ref<'benign' | 'threat'>('threat')

// Duration presets
const durationPresets = [
  { label: '1h', value: 1 }, { label: '4h', value: 4 },
  { label: '8h', value: 8 }, { label: '12h', value: 12 },
  { label: '24h', value: 24 }, { label: '48h', value: 48 },
  { label: '7d', value: 168 },
]

// Stats
const stats = ref({ totalEvents: 0, attackEvents: 0, detectionEvents: 0, warningEvents: 0, blockedEvents: 0, compromisedDevices: 0 })

// Event filter
const eventFilter = ref<string>('all')
const autoScroll = ref(true)
const eventLogRef = ref<HTMLDivElement | null>(null)
let eventOffset = 0

// Current simulation time (in minutes)
const currentSimTime = ref(0)

// WebSocket
const { data: wsData, connected: wsConnected } = useWebSocket('telemetry')

watch(wsData, (val) => {
  if (!val) return
  // Handle simulation_ended event — immediately refresh status
  if (val.event_type === 'simulation_ended') {
    loadStatus()
    return
  }
  const eventType = mapEventType(val.type || val.event_type || 'info')
  const newEvent: SimEvent = {
    id: `ws-${Date.now()}-${Math.random().toString(36).slice(2, 6)}`,
    timestamp: val.timestamp || new Date().toISOString(),
    type: eventType,
    message: val.message || val.details || JSON.stringify(val.data || val),
    deviceId: val.device_id || val.device,
    severity: val.severity,
  }
  eventLog.value.unshift(newEvent)
  if (eventLog.value.length > 500) eventLog.value.pop()
  updateStats(eventType)
  if (val.device_id) updateDeviceStatus(val.device_id, eventType)
  updateThreatStatus(eventType, val)
  if (autoScroll.value && eventLogRef.value) eventLogRef.value.scrollTop = 0
})

// ── Helpers ──
function mapEventType(type: string): SimEvent['type'] {
  if (type.includes('attack') || type.includes('threat') || type.includes('inject')) return 'attack'
  if (type.includes('detect') || type.includes('block') || type.includes('alert')) return 'detection'
  if (type.includes('warn') || type.includes('anomal')) return 'warning'
  if (type.includes('system') || type.includes('start') || type.includes('stop')) return 'system'
  return 'info'
}

function updateStats(type: SimEvent['type']) {
  stats.value.totalEvents++
  if (type === 'attack') stats.value.attackEvents++
  if (type === 'detection') stats.value.detectionEvents++
  if (type === 'warning') stats.value.warningEvents++
}

function updateDeviceStatus(deviceId: string, eventType: SimEvent['type']) {
  for (const room of rooms.value) {
    const device = room.devices.find(d => d.id === deviceId)
    if (device) {
      if (eventType === 'attack') { device.status = 'compromised'; stats.value.compromisedDevices++ }
      else if (eventType === 'warning') device.status = 'warning'
      else if (eventType === 'detection') device.status = 'normal'
      break
    }
  }
}

function updateThreatStatus(eventType: SimEvent['type'], data: any) {
  if (!data.threat_id && !data.threat_name) return
  const threat = threats.value.find(t =>
    t.id === data.threat_id || t.name === data.threat_name
  )
  if (!threat) return
  if (eventType === 'attack') threat.status = 'active'
  else if (eventType === 'detection') threat.status = 'detected'
}

function getDeviceStatusColor(status: string): string {
  switch (status) {
    case 'normal': return '#22c55e'
    case 'warning': return '#eab308'
    case 'compromised': return '#ef4444'
    case 'offline': return '#6b7280'
    default: return '#22c55e'
  }
}

function getEventTypeIcon(type: SimEvent['type']): string {
  switch (type) {
    case 'info': return 'ℹ️'; case 'warning': return '⚠️'
    case 'attack': return '🔴'; case 'detection': return '🛡️'; case 'system': return '⚙️'
    default: return '•'
  }
}

function getEventTypeColor(type: SimEvent['type']): string {
  switch (type) {
    case 'info': return '#3b82f6'; case 'warning': return '#eab308'
    case 'attack': return '#ef4444'; case 'detection': return '#22c55e'; case 'system': return '#6b7280'
    default: return '#6b7280'
  }
}

function getSeverityColor(severity: string): string {
  switch (severity) {
    case 'low': return '#22c55e'; case 'medium': return '#eab308'
    case 'high': return '#f97316'; case 'critical': return '#ef4444'
    default: return '#6b7280'
  }
}

function getThreatStatusColor(status: string): string {
  switch (status) {
    case 'pending': return '#6b7280'; case 'active': return '#eab308'
    case 'detected': return '#22c55e'; case 'blocked': return '#3b82f6'; case 'completed': return '#ef4444'
    default: return '#6b7280'
  }
}

function getThreatStatusIcon(status: string): string {
  switch (status) {
    case 'pending': return '⏳'; case 'active': return '⚡'
    case 'detected': return '🛡️'; case 'blocked': return '🔒'; case 'completed': return '✅'
    default: return '•'
  }
}

function formatTimestamp(ts: string): string {
  try {
    const d = new Date(ts)
    return d.toLocaleTimeString('en-US', { hour12: false, hour: '2-digit', minute: '2-digit', second: '2-digit' })
  } catch { return ts || '—' }
}

function formatTime(minutes: number): string {
  const h = Math.floor(minutes / 60)
  const m = Math.floor(minutes % 60)
  const s = Math.floor((minutes * 60) % 60)
  return `${String(h).padStart(2, '0')}:${String(m).padStart(2, '0')}:${String(s).padStart(2, '0')}`
}

function formatDurationShort(minutes: number): string {
  if (minutes < 60) return `${minutes}m`
  const h = Math.floor(minutes / 60)
  const m = minutes % 60
  return m > 0 ? `${h}h ${m}m` : `${h}h`
}

// ── Computed ──
const filteredEvents = computed(() => {
  if (eventFilter.value === 'all') return eventLog.value
  return eventLog.value.filter(e => e.type === eventFilter.value)
})

const simState = computed(() => simStatus.value?.simulation?.state || 'idle')
const isRunning = computed(() =>
  simStatus.value?.simulation_active === true && simState.value !== 'completed'
)
const isPaused = computed(() => simState.value === 'paused')
const isStopped = computed(() => !isRunning.value && !isPaused.value)

const progressPercent = computed(() => {
  const sim = simStatus.value?.simulation
  if (!sim) return 0
  if (sim.progress_percent !== undefined) return Math.min(100, sim.progress_percent)
  const elapsed = sim.elapsed_hours || 0
  const total = sim.duration_hours || duration.value
  return Math.min(100, (elapsed / total) * 100)
})

const totalDevices = computed(() => rooms.value.reduce((sum, r) => sum + r.devices.length, 0))
const totalDurationMinutes = computed(() => duration.value * 60)

// Threat markers for progress bar
const threatMarkers = computed(() => {
  return threats.value.map(t => ({
    id: t.id,
    position: Math.min(100, (t.startTime / totalDurationMinutes.value) * 100),
    color: getSeverityColor(t.severity),
    status: t.status,
    name: t.name,
  }))
})

// Estimated real time
const estimatedRealTime = computed(() => {
  const totalSimMinutes = duration.value * 60
  const compression = timeCompression.value * simulationSpeed.value
  const realMinutes = totalSimMinutes / compression
  if (realMinutes < 1) return '< 1 min'
  if (realMinutes < 60) return `~${Math.ceil(realMinutes)} min`
  return `~${(realMinutes / 60).toFixed(1)} hrs`
})

// ── Data Loading ──
function loadHomeFromStore() {
  if (homeStore.rooms.length > 0) {
    rooms.value = homeStore.rooms.map((r, i) => ({
      id: r.id, name: r.name, type: r.type || 'living_room',
      x: r.x ?? (30 + (i % 3) * 220),
      y: r.y ?? (30 + Math.floor(i / 3) * 170),
      width: r.width ?? 200, height: r.height ?? 150,
      devices: r.devices.map(d => ({
        id: d.id, name: d.name, type: d.type,
        icon: d.icon || getDeviceIcon(d.type),
        status: 'normal' as const,
      })),
    }))
  }
}

function loadThreatsFromStore() {
  if (threatStore.events.length > 0) {
    threats.value = threatStore.events.map(t => ({
      id: t.id, name: t.name, type: t.type,
      startTime: t.startTime, duration: t.duration,
      severity: t.severity, targetDevice: t.targetDevice,
      description: t.description,
      status: 'pending' as const,
    }))
    // Auto-enable threat mode if threats exist
    if (threats.value.length > 0) {
      simMode.value = 'threat'
      includeThreats.value = true
    }
  }
}

async function loadHomeConfig() {
  loadHomeFromStore()
  if (rooms.value.length > 0) return
  await homeStore.loadFromBackend()
  loadHomeFromStore()
  if (rooms.value.length > 0) return
  // Final fallback: direct API
  try {
    const [roomsResp, devicesResp] = await Promise.all([
      api.getRooms().catch(() => null), api.getHomeDevices().catch(() => null),
    ])
    const roomsData = roomsResp?.data || []
    const devicesData = devicesResp?.data || []
    const roomList = Array.isArray(roomsData) ? roomsData : (roomsData.rooms || [])
    const deviceList = Array.isArray(devicesData) ? devicesData : (devicesData.devices || [])
    if (roomList.length > 0) {
      rooms.value = roomList.map((r: any, i: number) => ({
        id: r.id, name: r.name, type: r.room_type || r.type || 'living_room',
        x: r.x ?? (30 + (i % 3) * 220), y: r.y ?? (30 + Math.floor(i / 3) * 170),
        width: r.width ?? 200, height: r.height ?? 150,
        devices: deviceList.filter((d: any) => d.room_id === r.id).map((d: any) => ({
          id: d.id, name: d.name, type: d.device_type || d.type,
          icon: getDeviceIcon(d.device_type || d.type), status: 'normal' as const,
        })),
      }))
    }
  } catch { /* ignore */ }
}

async function loadThreatsConfig() {
  loadThreatsFromStore()
  if (threats.value.length > 0) return
  await threatStore.loadFromBackend()
  loadThreatsFromStore()
}

async function loadStatus() {
  try {
    const resp = await api.getSimulationStatus()
    simStatus.value = resp.data
    // Update current sim time from nested simulation data
    const sim = resp.data?.simulation
    if (sim?.elapsed_hours) {
      currentSimTime.value = sim.elapsed_hours * 60
    } else if (sim?.progress_percent && sim?.progress_percent > 0) {
      // Estimate from progress if elapsed not available
      currentSimTime.value = (sim.progress_percent / 100) * duration.value * 60
    }
    // Update stats from S5-HES data
    if (sim?.total_events) {
      stats.value.totalEvents = sim.total_events
    }
    // Update threat statuses based on elapsed time
    if (isRunning.value || isPaused.value) {
      updateThreatStatuses()
    }
  } catch { /* ignore */ }
}

function updateThreatStatuses() {
  const elapsedMin = currentSimTime.value
  for (const t of threats.value) {
    if (t.status === 'detected' || t.status === 'blocked') continue // keep final statuses
    if (elapsedMin >= t.startTime + t.duration) t.status = 'completed'
    else if (elapsedMin >= t.startTime) t.status = 'active'
    else t.status = 'pending'
  }
}

async function loadEvents() {
  try {
    const resp = await api.getSimulationEvents({ limit: 100, offset: eventOffset })
    const evts = Array.isArray(resp.data) ? resp.data : (resp.data.events || [])
    if (evts.length === 0) return
    const newEvents: SimEvent[] = evts.map((ev: any, i: number) => ({
      id: ev.id || `evt-${eventOffset + i}`,
      timestamp: ev.timestamp || ev.time || '',
      type: mapEventType(ev.event_type || ev.type || 'info'),
      message: typeof ev.data === 'object' ? JSON.stringify(ev.data) : (ev.message || ev.details || ''),
      deviceId: ev.device_id || ev.device,
      severity: ev.severity,
    }))
    eventOffset += evts.length
    // Prepend new events (newest first)
    eventLog.value = [...newEvents.reverse(), ...eventLog.value]
    if (eventLog.value.length > 500) eventLog.value.length = 500
    // Update stats for new events
    for (const ev of newEvents) {
      updateStats(ev.type)
      if (ev.deviceId) updateDeviceStatus(ev.deviceId, ev.type)
      updateThreatStatus(ev.type, { threat_id: undefined, threat_name: undefined, ...ev })
    }
    if (autoScroll.value && eventLogRef.value) eventLogRef.value.scrollTop = 0
  } catch { /* ignore */ }
}

// ── Simulation Controls ──
async function startSim() {
  error.value = ''; loading.value = true
  try {
    await api.startSimulation({
      duration_hours: duration.value,
      time_compression: timeCompression.value * simulationSpeed.value,
      include_threats: simMode.value === 'threat' && includeThreats.value,
    })
    stats.value = { totalEvents: 0, attackEvents: 0, detectionEvents: 0, warningEvents: 0, blockedEvents: 0, compromisedDevices: 0 }
    eventLog.value = []
    eventOffset = 0
    currentSimTime.value = 0
    // Reset threat statuses
    for (const t of threats.value) t.status = 'pending'
    // Reset device statuses
    for (const r of rooms.value) {
      for (const d of r.devices) d.status = 'normal'
    }
    await loadStatus()
  } catch (e: any) {
    error.value = e.response?.data?.detail || 'Failed to start simulation'
  }
  loading.value = false
}

async function pauseSim() {
  loading.value = true
  try { await api.pauseSimulation(); await loadStatus() }
  catch (e: any) { error.value = e.response?.data?.detail || 'Failed to pause' }
  loading.value = false
}

async function resumeSim() {
  loading.value = true
  try { await api.resumeSimulation(); await loadStatus() }
  catch (e: any) { error.value = e.response?.data?.detail || 'Failed to resume' }
  loading.value = false
}

async function stopSim() {
  if (!confirm('Stop the simulation?')) return
  loading.value = true
  try { await api.stopSimulation(); await loadStatus() }
  catch (e: any) { error.value = e.response?.data?.detail || 'Failed to stop' }
  loading.value = false
}

function setSpeed(speed: number) { simulationSpeed.value = speed }
function setDuration(hours: number) { duration.value = hours }

function exportResults() {
  const data = {
    simulation: simStatus.value,
    home: { rooms: rooms.value },
    threats: threats.value,
    statistics: stats.value,
    events: eventLog.value,
    exportedAt: new Date().toISOString(),
  }
  const blob = new Blob([JSON.stringify(data, null, 2)], { type: 'application/json' })
  const url = URL.createObjectURL(blob)
  const a = document.createElement('a')
  a.href = url; a.download = `simulation-results-${Date.now()}.json`; a.click()
  URL.revokeObjectURL(url)
}

// Watch for store changes
watch(() => homeStore.rooms.length, () => { if (homeStore.rooms.length > 0) loadHomeFromStore() })
watch(() => threatStore.events.length, () => { if (threatStore.events.length > 0) loadThreatsFromStore() })

// Reload everything when session changes (create/teardown/resume)
watch(() => sessionStore.sessionName, async (newName, oldName) => {
  if (newName !== oldName) {
    // Reset local simulation state
    rooms.value = []
    threats.value = []
    events.value = []
    simStatus.value = null
    currentSimTime.value = 0
    // Reload from fresh backend data
    await loadHomeConfig()
    await loadThreatsConfig()
    await loadStatus()
  }
})

// ── Polling ──
let pollTimer: ReturnType<typeof setInterval>

onMounted(async () => {
  loadHomeConfig()
  if (!isRealOnly.value) {
    loadThreatsConfig()
    await loadStatus()
    if (isRunning.value || isPaused.value) {
      loadEvents()
    }
    pollTimer = setInterval(() => {
      loadStatus()
      if (isRunning.value) loadEvents()
    }, 3000)
  }
})

onUnmounted(() => clearInterval(pollTimer))
</script>

<template>
  <div class="simulation-view">
    <!-- Real-Mode: Header + Live Feed -->
    <template v-if="isRealOnly">
      <div class="sim-header">
        <div class="header-left">
          <h2 style="margin:0;font-size:18px">Live Monitor</h2>
          <span class="badge badge-success" style="font-size:10px">Real Mode</span>
          <span class="badge" :class="wsConnected ? 'badge-success' : 'badge-neutral'" style="font-size:10px">
            WS {{ wsConnected ? 'Live' : 'Off' }}
          </span>
        </div>
        <div class="header-right">
          <RouterLink to="/agents" class="btn btn-primary btn-sm">Agent Monitor</RouterLink>
          <button class="btn btn-ghost btn-sm" @click="loadStatus">↻</button>
        </div>
      </div>

      <!-- Real-mode 2-column layout: info + event log -->
      <div class="real-mode-content-grid">
        <!-- LEFT: Device & Session Info -->
        <div class="real-info-panel">
          <div class="panel-header">
            <span class="panel-title">Real Devices</span>
            <span class="panel-subtitle">{{ sessionStore.realDevices }} connected</span>
          </div>
          <div class="real-info-body">
            <div class="real-info-card">
              <div class="real-info-icon">📡</div>
              <div>
                <div style="font-size:13px;font-weight:600">{{ sessionStore.realDevices }} Real Device{{ sessionStore.realDevices !== 1 ? 's' : '' }}</div>
                <div style="font-size:11px;color:var(--text-muted)">Connected via protocol adapters</div>
              </div>
            </div>
            <div class="stat-grid" style="margin-top:12px">
              <div class="stat-card">
                <div class="label">Total Events</div>
                <div class="value">{{ stats.totalEvents }}</div>
              </div>
              <div class="stat-card">
                <div class="label">Warnings</div>
                <div class="value" style="color:var(--warning)">{{ stats.warningEvents }}</div>
              </div>
            </div>
            <div style="margin-top:16px;font-size:11px;color:var(--text-muted);line-height:1.5;padding:8px;background:var(--bg-input);border-radius:6px">
              S5-HES behavioral simulation is disabled in real mode.
              Telemetry comes directly from physical devices.
              Use <RouterLink to="/agents" style="color:var(--accent)">Agent Monitor</RouterLink> to run decision cycles.
            </div>
          </div>
        </div>

        <!-- RIGHT: Live Event Log (reuses the same event log component) -->
        <div class="sim-event-log">
          <div class="event-log-header">
            <span class="panel-title">Live Event Log ({{ stats.totalEvents }})</span>
            <div style="display:flex;gap:4px">
              <button v-for="f in [
                { key: 'all', label: 'All' },
                { key: 'attack', label: '🔴' },
                { key: 'detection', label: '🛡️' },
                { key: 'warning', label: '⚠️' },
                { key: 'info', label: 'ℹ️' },
                { key: 'system', label: '⚙️' },
              ]" :key="f.key" class="filter-btn" :class="{ active: eventFilter === f.key }"
                @click="eventFilter = f.key" :title="f.key">{{ f.label }}</button>
            </div>
          </div>
          <div ref="eventLogRef" class="event-log-content">
            <div v-if="filteredEvents.length === 0" class="empty-state" style="padding:24px">
              Waiting for device events... Run an agent cycle from the Agent Monitor to generate activity.
            </div>
            <div v-for="ev in filteredEvents" :key="ev.id" class="log-entry"
              :style="{ borderLeftColor: getEventTypeColor(ev.type) }">
              <div class="log-entry-header">
                <span style="font-size:12px">{{ getEventTypeIcon(ev.type) }}</span>
                <span class="event-type-badge" :style="{
                  backgroundColor: getEventTypeColor(ev.type) + '25',
                  color: getEventTypeColor(ev.type),
                  border: '1px solid ' + getEventTypeColor(ev.type) + '40',
                }">{{ ev.type }}</span>
                <span v-if="ev.deviceId" class="event-device">{{ ev.deviceId }}</span>
                <span class="event-timestamp">{{ formatTimestamp(ev.timestamp) }}</span>
              </div>
              <div class="log-entry-message">{{ ev.message }}</div>
            </div>
          </div>
        </div>
      </div>
    </template>

    <!-- Simulation UI (hidden in real-only mode) -->
    <template v-if="!isRealOnly">
    <!-- Header -->
    <div class="sim-header">
      <div class="header-left">
        <h2 style="margin:0;font-size:18px">Simulation</h2>
        <span class="badge" :class="wsConnected ? 'badge-success' : 'badge-neutral'" style="font-size:10px">
          WS {{ wsConnected ? 'Live' : 'Off' }}
        </span>
        <span v-if="sessionStore.deviceMode === 'hybrid'" class="badge badge-warning" style="font-size:10px">Hybrid</span>
        <span v-if="isRunning" class="badge badge-success" style="font-size:10px;animation:pulse 2s infinite">Running</span>
        <span v-else-if="isPaused" class="badge badge-warning" style="font-size:10px">Paused</span>
        <span v-else class="badge badge-neutral" style="font-size:10px">Idle</span>
      </div>
      <div class="header-right">
        <!-- Mode Toggle -->
        <div class="mode-toggle">
          <button :class="['mode-btn', { active: simMode === 'benign' }]" @click="simMode = 'benign'; includeThreats = false">Benign</button>
          <button :class="['mode-btn', { active: simMode === 'threat' }]" @click="simMode = 'threat'; includeThreats = true">Threat</button>
        </div>
        <!-- Speed selector -->
        <div class="speed-selector">
          <button v-for="s in [1, 2, 5, 10]" :key="s" class="speed-btn" :class="{ active: simulationSpeed === s }" @click="setSpeed(s)">{{ s }}x</button>
        </div>
        <span class="divider-v"></span>
        <!-- Controls -->
        <template v-if="isStopped">
          <button class="btn btn-success btn-sm" @click="startSim" :disabled="loading || !simStatus?.s5_hes_available">
            {{ loading ? 'Starting...' : '▶ Start' }}
          </button>
        </template>
        <template v-else>
          <button v-if="isRunning" class="btn btn-secondary btn-sm" @click="pauseSim" :disabled="loading">⏸ Pause</button>
          <button v-if="isPaused" class="btn btn-primary btn-sm" @click="resumeSim" :disabled="loading">▶ Resume</button>
          <button class="btn btn-danger btn-sm" @click="stopSim" :disabled="loading">⏹ Stop</button>
        </template>
        <button class="btn btn-ghost btn-sm" @click="exportResults" :disabled="eventLog.length === 0" title="Export Results">📥</button>
        <button class="btn btn-ghost btn-sm" @click="loadStatus(); loadEvents(); loadHomeConfig(); loadThreatsConfig()">↻</button>
      </div>
    </div>

    <!-- Error -->
    <div v-if="error" class="error-bar">
      <span class="text-danger">{{ error }}</span>
      <button class="btn btn-ghost btn-sm" @click="error=''">Dismiss</button>
    </div>

    <!-- Progress Bar with Threat Markers -->
    <div class="progress-section">
      <div class="progress-row">
        <span class="time-display">{{ formatTime(currentSimTime) }}</span>
        <div class="progress-bar-container">
          <div class="progress-bar-track">
            <div class="progress-bar-fill" :style="{ width: progressPercent + '%' }"></div>
            <!-- Threat markers -->
            <div v-for="marker in threatMarkers" :key="marker.id"
              class="threat-marker" :class="{ active: marker.status === 'active' }"
              :style="{ left: marker.position + '%', backgroundColor: marker.color }"
              :title="`${marker.name} (${marker.status})`"
            ></div>
          </div>
        </div>
        <span class="time-display">{{ formatTime(totalDurationMinutes) }}</span>
      </div>
      <!-- Stats row -->
      <div class="stats-row">
        <div class="mini-stat">
          <span class="mini-stat-value">{{ stats.totalEvents }}</span>
          <span class="mini-stat-label">Events</span>
        </div>
        <div class="mini-stat">
          <span class="mini-stat-value" style="color:#ef4444">{{ stats.attackEvents }}</span>
          <span class="mini-stat-label">Attacks</span>
        </div>
        <div class="mini-stat">
          <span class="mini-stat-value" style="color:#22c55e">{{ stats.detectionEvents }}</span>
          <span class="mini-stat-label">Detected</span>
        </div>
        <div class="mini-stat">
          <span class="mini-stat-value" style="color:#eab308">{{ stats.warningEvents }}</span>
          <span class="mini-stat-label">Warnings</span>
        </div>
        <div class="mini-stat">
          <span class="mini-stat-value" style="color:#ef4444">{{ stats.compromisedDevices }}</span>
          <span class="mini-stat-label">Compromised</span>
        </div>
        <div style="flex:1"></div>
        <span class="est-time">Est. real time: {{ estimatedRealTime }}</span>
      </div>
    </div>

    <!-- 3-Column Content -->
    <div class="sim-content">
      <!-- LEFT: Home Visualization -->
      <div class="sim-home-panel">
        <div class="panel-header">
          <span class="panel-title">Home Environment</span>
          <span class="panel-subtitle">{{ rooms.length }} rooms, {{ totalDevices }} devices</span>
        </div>
        <div class="sim-home-map">
          <div v-if="rooms.length === 0" class="empty-state">
            <div style="font-size:48px">🏠</div>
            <div style="font-size:14px;font-weight:500">No Home Configuration</div>
            <div style="font-size:12px">Configure rooms in the Home Builder first</div>
          </div>
          <div v-for="room in rooms" :key="room.id" class="sim-room"
            :style="{
              left: room.x + 'px', top: room.y + 'px',
              width: room.width + 'px', height: room.height + 'px',
              borderColor: (roomTypeConfig[room.type]?.color || '#6b7280') + '60',
              backgroundColor: (roomTypeConfig[room.type]?.color || '#6b7280') + '10',
            }">
            <div class="sim-room-header" :style="{ borderBottomColor: (roomTypeConfig[room.type]?.color || '#6b7280') + '30' }">
              <span>{{ roomTypeConfig[room.type]?.icon || '🏠' }}</span>
              <span class="room-label">{{ room.name }}</span>
            </div>
            <div class="sim-device-grid">
              <div v-for="device in room.devices" :key="device.id" class="sim-device" :class="device.status"
                :title="`${device.name} (${device.status})`">
                <span style="font-size:14px">{{ device.icon }}</span>
                <div class="device-status-dot" :style="{ backgroundColor: getDeviceStatusColor(device.status) }"></div>
              </div>
            </div>
          </div>
        </div>
        <!-- Device Legend -->
        <div class="device-legend">
          <span class="legend-item"><span class="legend-dot" style="background:#22c55e"></span>Normal</span>
          <span class="legend-item"><span class="legend-dot" style="background:#eab308"></span>Warning</span>
          <span class="legend-item"><span class="legend-dot" style="background:#ef4444"></span>Compromised</span>
          <span class="legend-item"><span class="legend-dot" style="background:#6b7280"></span>Offline</span>
        </div>
      </div>

      <!-- CENTER: Threat Timeline -->
      <div class="sim-threat-panel">
        <div class="panel-header">
          <span class="panel-title">Threat Timeline</span>
          <span class="panel-subtitle">{{ threats.length }} threats</span>
        </div>
        <div class="threat-timeline-scroll">
          <div v-if="threats.length === 0 && simMode === 'threat'" class="empty-state" style="padding:24px">
            <div style="font-size:36px">🛡️</div>
            <div style="font-size:13px;font-weight:500">No Threats Configured</div>
            <div style="font-size:11px">Add threats in the Threat Builder first</div>
          </div>
          <div v-else-if="simMode === 'benign'" class="empty-state" style="padding:24px">
            <div style="font-size:36px">🌿</div>
            <div style="font-size:13px;font-weight:500">Benign Mode</div>
            <div style="font-size:11px">No threats active — simulating normal activity</div>
          </div>

          <!-- Timeline -->
          <div v-if="threats.length > 0 && simMode === 'threat'" class="threat-timeline">
            <div class="timeline-line"></div>
            <div v-for="(threat, idx) in threats" :key="threat.id" class="threat-card"
              :class="threat.status"
              :style="{ borderLeftColor: getThreatStatusColor(threat.status) }">
              <!-- Timeline dot -->
              <div class="timeline-dot" :style="{ backgroundColor: getThreatStatusColor(threat.status) }"></div>
              <!-- Time marker -->
              <div class="threat-time">{{ formatDurationShort(threat.startTime) }}</div>
              <!-- Card content -->
              <div class="threat-card-content">
                <div class="threat-card-header">
                  <span class="threat-status-icon">{{ getThreatStatusIcon(threat.status) }}</span>
                  <span class="threat-name">{{ threat.name }}</span>
                  <span class="severity-badge" :style="{ backgroundColor: getSeverityColor(threat.severity) }">{{ threat.severity }}</span>
                </div>
                <div v-if="threat.description" class="threat-desc">{{ threat.description }}</div>
                <div class="threat-meta">
                  <span>⏱ {{ formatDurationShort(threat.duration) }}</span>
                  <span v-if="threat.targetDevice">🎯 {{ threat.targetDevice }}</span>
                  <span class="threat-status-label" :style="{ color: getThreatStatusColor(threat.status) }">{{ threat.status }}</span>
                </div>
              </div>
            </div>
          </div>
        </div>
      </div>

      <!-- RIGHT: Config + Event Log -->
      <div class="sim-log-panel">
        <!-- Config (when stopped) -->
        <div v-if="isStopped" class="sim-config-section">
          <div style="font-size:12px;font-weight:600;color:var(--text-primary);margin-bottom:8px">Configuration</div>
          <!-- Duration presets -->
          <div style="margin-bottom:8px">
            <label class="config-label">Duration</label>
            <div class="duration-presets">
              <button v-for="p in durationPresets" :key="p.value"
                class="preset-btn" :class="{ active: duration === p.value }"
                @click="setDuration(p.value)">{{ p.label }}</button>
            </div>
          </div>
          <div style="display:grid;grid-template-columns:1fr 1fr;gap:8px">
            <div>
              <label class="config-label">Duration (hrs)</label>
              <input class="form-input" type="number" v-model.number="duration" min="1" max="168" style="font-size:12px" />
            </div>
            <div>
              <label class="config-label">Compression</label>
              <input class="form-input" type="number" v-model.number="timeCompression" min="1" max="3600" style="font-size:12px" />
            </div>
          </div>
          <label v-if="simMode === 'threat'" class="checkbox-label">
            <input type="checkbox" v-model="includeThreats" />
            Include configured threats
          </label>
          <div v-if="!simStatus?.s5_hes_available" class="s5-warning">
            <span>⚠️</span> S5-HES must be online to start simulation
          </div>
        </div>

        <!-- Event Log -->
        <div class="sim-event-log">
          <div class="event-log-header">
            <span class="panel-title">Event Log ({{ stats.totalEvents.toLocaleString() }})</span>
            <div style="display:flex;gap:4px">
              <button v-for="f in [
                { key: 'all', label: 'All' },
                { key: 'attack', label: '🔴' },
                { key: 'detection', label: '🛡️' },
                { key: 'warning', label: '⚠️' },
                { key: 'info', label: 'ℹ️' },
                { key: 'system', label: '⚙️' },
              ]" :key="f.key" class="filter-btn" :class="{ active: eventFilter === f.key }"
                @click="eventFilter = f.key" :title="f.key">{{ f.label }}</button>
            </div>
          </div>
          <div ref="eventLogRef" class="event-log-content">
            <div v-if="filteredEvents.length === 0" class="empty-state" style="padding:24px">
              {{ eventLog.length === 0 ? 'No events yet. Start a simulation to see live events.' : 'No events match this filter.' }}
            </div>
            <div v-for="ev in filteredEvents" :key="ev.id" class="log-entry"
              :style="{ borderLeftColor: getEventTypeColor(ev.type) }">
              <div class="log-entry-header">
                <span style="font-size:12px">{{ getEventTypeIcon(ev.type) }}</span>
                <span class="event-type-badge" :style="{
                  backgroundColor: getEventTypeColor(ev.type) + '25',
                  color: getEventTypeColor(ev.type),
                  border: '1px solid ' + getEventTypeColor(ev.type) + '40',
                }">{{ ev.type }}</span>
                <span v-if="ev.deviceId" class="event-device">{{ ev.deviceId }}</span>
                <span class="event-timestamp">{{ formatTimestamp(ev.timestamp) }}</span>
              </div>
              <div class="log-entry-message">{{ ev.message }}</div>
            </div>
          </div>
        </div>
      </div>
    </div>
    </template><!-- end v-if="!isRealOnly" -->
  </div>
</template>

<style scoped>
/* ── Real Mode Layout ── */
.real-mode-content-grid {
  display: grid;
  grid-template-columns: 320px 1fr;
  gap: 10px;
  flex: 1;
  overflow: hidden;
  padding-bottom: 8px;
}
.real-info-panel {
  display: flex;
  flex-direction: column;
  background: var(--bg-secondary);
  border-radius: 8px;
  border: 1px solid var(--border);
  overflow: hidden;
}
.real-info-body {
  padding: 12px;
  flex: 1;
}
.real-info-card {
  display: flex;
  align-items: center;
  gap: 12px;
  padding: 12px;
  background: var(--bg-input);
  border-radius: 8px;
  border: 1px solid var(--border);
}
.real-info-icon {
  font-size: 28px;
  flex-shrink: 0;
}
@media (max-width: 900px) {
  .real-mode-content-grid {
    grid-template-columns: 1fr;
  }
}

.simulation-view {
  display: flex;
  flex-direction: column;
  height: calc(100vh - 100px);
  gap: 10px;
}

/* ── Header ── */
.sim-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
  padding: 10px 16px;
  background: var(--bg-secondary);
  border-radius: 8px;
  flex-shrink: 0;
  flex-wrap: wrap;
  gap: 8px;
}
.header-left, .header-right { display: flex; align-items: center; gap: 8px; flex-wrap: wrap; }

.mode-toggle {
  display: flex;
  background: var(--bg-input);
  border-radius: 6px;
  padding: 2px;
}
.mode-btn {
  padding: 3px 12px;
  font-size: 11px;
  font-weight: 600;
  border: none;
  background: transparent;
  color: var(--text-muted);
  border-radius: 4px;
  cursor: pointer;
  transition: all 0.15s;
}
.mode-btn:hover { color: var(--text-primary); }
.mode-btn.active { background: var(--accent); color: white; }

.speed-selector { display: flex; gap: 2px; background: var(--bg-input); border-radius: 6px; padding: 2px; }
.speed-btn {
  padding: 3px 10px; font-size: 11px; font-weight: 600;
  border: none; background: transparent; color: var(--text-muted);
  border-radius: 4px; cursor: pointer; transition: all 0.15s;
}
.speed-btn:hover { color: var(--text-primary); }
.speed-btn.active { background: var(--accent); color: white; }
.divider-v { width: 1px; height: 20px; background: var(--border-primary); }

/* ── Error ── */
.error-bar {
  display: flex; align-items: center; justify-content: space-between;
  padding: 8px 16px; border: 1px solid var(--danger); border-radius: 6px; background: var(--bg-secondary);
}

/* ── Progress Section ── */
.progress-section { padding: 0 4px; flex-shrink: 0; }
.progress-row { display: flex; align-items: center; gap: 10px; }
.time-display {
  font-family: monospace; font-size: 13px; font-weight: 700;
  color: var(--text-primary); min-width: 70px; text-align: center;
}
.progress-bar-container { flex: 1; }
.progress-bar-track {
  position: relative; height: 8px; background: var(--bg-input);
  border-radius: 4px; overflow: visible;
}
.progress-bar-fill {
  height: 100%; background: linear-gradient(90deg, var(--accent), #22c55e);
  border-radius: 4px; transition: width 0.5s;
}
.threat-marker {
  position: absolute; top: -3px; width: 8px; height: 14px;
  border-radius: 2px; transform: translateX(-50%);
  opacity: 0.8; cursor: help; transition: all 0.2s;
}
.threat-marker:hover { opacity: 1; transform: translateX(-50%) scaleY(1.3); }
.threat-marker.active { animation: markerPulse 1.5s infinite; }
@keyframes markerPulse {
  0%, 100% { opacity: 0.8; } 50% { opacity: 0.4; }
}

.stats-row {
  display: flex; align-items: center; gap: 8px; margin-top: 8px; flex-wrap: wrap;
}
.mini-stat {
  display: flex; flex-direction: column; align-items: center;
  padding: 4px 10px; background: var(--bg-secondary); border-radius: 6px;
  border: 1px solid var(--border-primary); min-width: 52px;
}
.mini-stat-value { font-size: 15px; font-weight: 700; color: var(--text-primary); }
.mini-stat-label { font-size: 9px; color: var(--text-muted); text-transform: uppercase; letter-spacing: 0.3px; }
.est-time { font-size: 11px; color: var(--text-muted); font-style: italic; }

/* ── 3-Column Content ── */
.sim-content {
  display: grid;
  grid-template-columns: 1fr 280px 320px;
  gap: 10px;
  flex: 1;
  overflow: hidden;
  padding-bottom: 8px;
}

/* ── Home Panel ── */
.sim-home-panel {
  display: flex; flex-direction: column; background: var(--bg-secondary);
  border-radius: 8px; border: 1px solid var(--border-primary); overflow: hidden;
}
.panel-header {
  display: flex; justify-content: space-between; align-items: center;
  padding: 8px 12px; border-bottom: 1px solid var(--border-primary); flex-shrink: 0;
}
.panel-title { font-size: 13px; font-weight: 600; color: var(--text-primary); }
.panel-subtitle { font-size: 11px; color: var(--text-muted); }

.sim-home-map {
  flex: 1; position: relative; overflow: auto;
  background-image: radial-gradient(circle, rgba(255,255,255,0.03) 1px, transparent 1px);
  background-size: 20px 20px; min-height: 200px;
}
.sim-room {
  position: absolute; border: 1px solid; border-radius: 8px;
  overflow: hidden; transition: box-shadow 0.2s;
}
.sim-room:hover { box-shadow: 0 2px 12px rgba(0,0,0,0.2); }
.sim-room-header {
  display: flex; align-items: center; gap: 6px;
  padding: 5px 8px; border-bottom: 1px solid; font-size: 12px;
}
.room-label { font-size: 11px; font-weight: 600; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.sim-device-grid { display: flex; flex-wrap: wrap; gap: 5px; padding: 6px; }
.sim-device {
  position: relative; width: 30px; height: 30px;
  display: flex; align-items: center; justify-content: center;
  background: var(--bg-input); border-radius: 6px; border: 1px solid var(--border-primary);
  cursor: default; transition: all 0.2s;
}
.sim-device.warning { border-color: #eab308; animation: devicePulse 2s infinite; }
.sim-device.compromised { border-color: #ef4444; background: rgba(239, 68, 68, 0.1); }
.sim-device.offline { opacity: 0.4; }
@keyframes devicePulse {
  0%, 100% { box-shadow: 0 0 0 0 rgba(234, 179, 8, 0.3); }
  50% { box-shadow: 0 0 0 4px rgba(234, 179, 8, 0); }
}
.device-status-dot {
  position: absolute; bottom: -2px; right: -2px;
  width: 7px; height: 7px; border-radius: 50%; border: 1.5px solid var(--bg-secondary);
}
.device-legend {
  display: flex; gap: 12px; padding: 6px 12px;
  border-top: 1px solid var(--border-primary);
  font-size: 10px; color: var(--text-muted); flex-shrink: 0;
}
.legend-item { display: flex; align-items: center; gap: 4px; }
.legend-dot { width: 8px; height: 8px; border-radius: 50%; }

/* ── Threat Timeline Panel ── */
.sim-threat-panel {
  display: flex; flex-direction: column; background: var(--bg-secondary);
  border-radius: 8px; border: 1px solid var(--border-primary); overflow: hidden;
}
.threat-timeline-scroll { flex: 1; overflow-y: auto; padding: 8px; }
.threat-timeline { position: relative; padding-left: 20px; }
.timeline-line {
  position: absolute; left: 9px; top: 0; bottom: 0;
  width: 2px; background: var(--border-primary);
}
.threat-card {
  position: relative; margin-bottom: 12px;
  border-left: 3px solid; border-radius: 0 8px 8px 0;
  background: var(--bg-input); padding: 10px 12px;
  transition: all 0.2s;
}
.threat-card.active {
  background: rgba(234, 179, 8, 0.08);
  box-shadow: 0 0 8px rgba(234, 179, 8, 0.15);
}
.threat-card.detected { background: rgba(34, 197, 94, 0.06); }
.threat-card.blocked { background: rgba(59, 130, 246, 0.06); }
.threat-card.completed { background: rgba(239, 68, 68, 0.06); }
.timeline-dot {
  position: absolute; left: -25px; top: 14px;
  width: 10px; height: 10px; border-radius: 50%;
  border: 2px solid var(--bg-secondary); z-index: 1;
}
.threat-card.active .timeline-dot { animation: dotPulse 1.5s infinite; }
@keyframes dotPulse {
  0%, 100% { box-shadow: 0 0 0 0 rgba(234, 179, 8, 0.4); }
  50% { box-shadow: 0 0 0 6px rgba(234, 179, 8, 0); }
}
.threat-time {
  position: absolute; left: -24px; top: 28px;
  font-size: 9px; color: var(--text-muted); font-family: monospace;
  transform: translateX(-100%); white-space: nowrap;
  display: none; /* hide on small widths, shown via container width */
}
.threat-card-content { display: flex; flex-direction: column; gap: 4px; }
.threat-card-header { display: flex; align-items: center; gap: 6px; }
.threat-status-icon { font-size: 14px; }
.threat-name { font-size: 12px; font-weight: 600; color: var(--text-primary); flex: 1; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.severity-badge {
  font-size: 9px; font-weight: 600; color: white; padding: 1px 6px;
  border-radius: 3px; text-transform: uppercase; letter-spacing: 0.3px;
}
.threat-desc { font-size: 11px; color: var(--text-secondary); line-height: 1.3; }
.threat-meta {
  display: flex; gap: 10px; font-size: 10px; color: var(--text-muted); flex-wrap: wrap;
}
.threat-status-label { font-weight: 600; text-transform: capitalize; }

/* ── Right Panel: Config + Event Log ── */
.sim-log-panel { display: flex; flex-direction: column; gap: 10px; overflow: hidden; }
.sim-config-section {
  background: var(--bg-secondary); border: 1px solid var(--border-primary);
  border-radius: 8px; padding: 12px; flex-shrink: 0;
}
.config-label { font-size: 10px; color: var(--text-muted); display: block; margin-bottom: 3px; }
.duration-presets { display: flex; gap: 3px; flex-wrap: wrap; }
.preset-btn {
  padding: 3px 8px; font-size: 10px; font-weight: 600;
  border: 1px solid var(--border-primary); background: transparent;
  color: var(--text-muted); border-radius: 4px; cursor: pointer; transition: all 0.15s;
}
.preset-btn:hover { color: var(--text-primary); border-color: var(--text-muted); }
.preset-btn.active { background: var(--accent); color: white; border-color: var(--accent); }
.checkbox-label {
  display: flex; align-items: center; gap: 6px;
  margin-top: 8px; font-size: 12px; color: var(--text-secondary); cursor: pointer;
}
.s5-warning {
  margin-top: 8px; font-size: 11px; color: var(--danger);
  display: flex; align-items: center; gap: 4px;
}

/* ── Event Log ── */
.sim-event-log {
  flex: 1; display: flex; flex-direction: column;
  background: var(--bg-secondary); border: 1px solid var(--border-primary);
  border-radius: 8px; overflow: hidden;
}
.event-log-header {
  display: flex; justify-content: space-between; align-items: center;
  padding: 8px 12px; border-bottom: 1px solid var(--border-primary); flex-shrink: 0;
}
.event-log-content { flex: 1; overflow-y: auto; padding: 4px; }
.filter-btn {
  padding: 2px 6px; font-size: 12px;
  border: 1px solid var(--border-primary); background: transparent;
  border-radius: 4px; cursor: pointer; transition: all 0.15s;
}
.filter-btn:hover { background: var(--bg-hover); }
.filter-btn.active { background: var(--accent); border-color: var(--accent); }
.log-entry {
  padding: 5px 8px; border-left: 3px solid; border-radius: 0 4px 4px 0;
  margin-bottom: 3px; background: var(--bg-input); transition: background 0.15s;
}
.log-entry:hover { background: var(--bg-hover); }
.log-entry-header { display: flex; align-items: center; gap: 6px; margin-bottom: 1px; }
.event-type-badge { font-size: 9px; padding: 0 4px; border-radius: 3px; }
.event-device { font-size: 10px; color: var(--text-muted); font-family: monospace; }
.event-timestamp { margin-left: auto; font-size: 10px; color: var(--text-muted); font-family: monospace; }
.log-entry-message { font-size: 11px; color: var(--text-secondary); padding-left: 22px; word-break: break-word; }

/* ── Utility ── */
.empty-state {
  display: flex; align-items: center; justify-content: center;
  flex-direction: column; gap: 6px; color: var(--text-muted);
  height: 100%; text-align: center;
}

@keyframes pulse {
  0%, 100% { opacity: 1; } 50% { opacity: 0.6; }
}

/* ── Responsive: collapse to 2 columns on narrower screens ── */
@media (max-width: 1100px) {
  .sim-content { grid-template-columns: 1fr 280px; }
  .sim-threat-panel { display: none; }
}
</style>
