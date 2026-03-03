<script setup lang="ts">
import { ref, computed, onMounted } from 'vue'
import { useSessionStore } from '@/stores/session'
import { useHomeBuilderStore } from '@/stores/homeBuilder'
import { useThreatBuilderStore } from '@/stores/threatBuilder'
import * as api from '@/services/apiClient'

const session = useSessionStore()
const homeBuilderStore = useHomeBuilderStore()
const threatBuilderStore = useThreatBuilderStore()
const sessions = ref<any[]>([])
const presets = ref<any[]>([])
const protocols = ref<string[]>([])
const loading = ref(false)
const showCreate = ref(false)
const newName = ref('')
const newPreset = ref('')
const error = ref('')

// Device mode
const deviceMode = ref<'simulation' | 'real' | 'hybrid'>('simulation')
const realDevices = ref<RealDeviceEntry[]>([])
const testResults = ref<Record<string, any>>({})
const testingDevice = ref<string | null>(null)

interface RealDeviceEntry {
  device_id: string
  device_type: string
  room: string
  protocol: string
  host: string
  port: number
  topic: string
  endpoint: string
  auth: Record<string, string>
}

const DEVICE_TYPES = [
  'thermostat', 'smart_lock', 'camera', 'smoke_detector',
  'light', 'motion_sensor', 'air_quality', 'water_leak',
  'energy_monitor', 'medical_alert',
  // Common real-world types
  'smart_plug', 'smart_speaker', 'doorbell', 'garage_door',
  'sprinkler', 'robot_vacuum', 'smart_tv', 'weather_station',
  'custom',
]

// Track which devices use custom type input
const customTypeActive = ref<Record<number, boolean>>({})

function addRealDevice() {
  realDevices.value.push({
    device_id: `real-device-${realDevices.value.length + 1}`,
    device_type: 'thermostat',
    room: 'living_room',
    protocol: protocols.value[0] || 'mock',
    host: 'localhost',
    port: 1883,
    topic: '',
    endpoint: '',
    auth: {},
  })
}

function removeRealDevice(index: number) {
  const dev = realDevices.value[index]
  if (dev) delete testResults.value[dev.device_id]
  realDevices.value.splice(index, 1)
}

async function testConnection(index: number) {
  const dev = realDevices.value[index]
  if (!dev) return
  testingDevice.value = dev.device_id
  try {
    const resp = await api.testDeviceConnection({
      device_id: dev.device_id,
      device_type: dev.device_type,
      room: dev.room,
      protocol: dev.protocol,
      host: dev.host,
      port: dev.port,
      topic: dev.topic,
      endpoint: dev.endpoint,
      auth: dev.auth,
    })
    testResults.value[dev.device_id] = resp.data
  } catch (e: any) {
    testResults.value[dev.device_id] = { ok: false, msg: e.response?.data?.detail || 'Connection failed' }
  }
  testingDevice.value = null
}

const showRealDeviceConfig = computed(() => deviceMode.value === 'real' || deviceMode.value === 'hybrid')

const canCreate = computed(() => {
  if (!newName.value.trim()) return false
  if (showRealDeviceConfig.value && deviceMode.value === 'real' && realDevices.value.length === 0) return false
  return true
})

async function loadSessions() {
  loading.value = true
  try {
    const resp = await api.listSessions()
    sessions.value = resp.data
  } catch { /* ignore */ }
  loading.value = false
}

async function loadPresets() {
  try {
    const resp = await api.getPresets()
    presets.value = resp.data
  } catch { /* ignore */ }
}

async function loadProtocols() {
  try {
    const resp = await api.getProtocols()
    protocols.value = resp.data.protocols || []
  } catch {
    protocols.value = ['mock', 'mqtt', 'http']
  }
}

async function createSession() {
  if (!canCreate.value) return
  error.value = ''
  loading.value = true
  // Clear cached builder data before creating new session
  homeBuilderStore.clear()
  homeBuilderStore.loaded = false
  threatBuilderStore.clear()
  threatBuilderStore.loaded = false
  try {
    const payload: any = {
      name: newName.value.trim(),
      preset: newPreset.value || undefined,
      device_mode: deviceMode.value,
    }
    if (showRealDeviceConfig.value && realDevices.value.length > 0) {
      payload.real_devices = realDevices.value
    }
    await api.createSession(payload)
    showCreate.value = false
    newName.value = ''
    newPreset.value = ''
    deviceMode.value = 'simulation'
    realDevices.value = []
    testResults.value = {}
    await loadSessions()
    await session.refresh()
  } catch (e: any) {
    error.value = e.response?.data?.detail || 'Failed to create session'
  }
  loading.value = false
}

async function resumeSess(name: string) {
  loading.value = true
  // Clear cached builder data so resumed session's data loads fresh
  homeBuilderStore.clear()
  homeBuilderStore.loaded = false
  threatBuilderStore.clear()
  threatBuilderStore.loaded = false
  try {
    await api.resumeSession(name)
    await session.refresh()
    await loadSessions()
  } catch (e: any) {
    error.value = e.response?.data?.detail || 'Failed to resume session'
  }
  loading.value = false
}

async function saveSess(name: string) {
  try {
    await api.saveSession(name)
  } catch { /* ignore */ }
}

async function deleteSess(name: string) {
  if (!confirm(`Delete session "${name}"?`)) return
  loading.value = true
  try {
    await api.deleteSession(name)
    await loadSessions()
    await session.refresh()
  } catch { /* ignore */ }
  loading.value = false
}

async function teardown() {
  if (!confirm('Teardown the current session?')) return
  loading.value = true
  try {
    await api.teardownSession()
    // Clear cached builder data so next session starts fresh
    homeBuilderStore.clear()
    homeBuilderStore.loaded = false
    threatBuilderStore.clear()
    threatBuilderStore.loaded = false
    await session.refresh()
    await loadSessions()
  } catch { /* ignore */ }
  loading.value = false
}

onMounted(() => {
  loadSessions()
  loadPresets()
  loadProtocols()
})
</script>

<template>
  <div>
    <div class="flex-between mb-16">
      <h1 class="page-title" style="margin-bottom:0">Sessions</h1>
      <div class="flex gap-8">
        <button v-if="session.active" class="btn btn-danger btn-sm" @click="teardown" :disabled="loading">
          Teardown Active
        </button>
        <button class="btn btn-primary btn-sm" @click="showCreate = !showCreate" :disabled="loading">
          + New Session
        </button>
      </div>
    </div>

    <div v-if="error" class="card" style="border-color:var(--danger);margin-bottom:12px">
      <span class="text-danger">{{ error }}</span>
      <button class="btn btn-sm btn-secondary" style="margin-left:12px" @click="error=''">Dismiss</button>
    </div>

    <!-- Create Form -->
    <div v-if="showCreate" class="card">
      <div class="card-header"><h3>Create New Session</h3></div>

      <!-- Basic Info -->
      <div class="form-row">
        <div class="form-group">
          <label>Session Name</label>
          <input class="form-input" v-model="newName" placeholder="my-session" @keyup.enter="createSession" />
        </div>
        <div class="form-group">
          <label>Preset (optional)</label>
          <select class="form-select" v-model="newPreset">
            <option value="">None</option>
            <option v-for="p in presets" :key="p.name || p" :value="p.name || p">
              {{ p.name || p }}
            </option>
          </select>
        </div>
      </div>

      <!-- Device Mode Selector -->
      <div class="form-group">
        <label>Device Mode</label>
        <div class="mode-selector">
          <div
            class="mode-option"
            :class="{ active: deviceMode === 'simulation' }"
            @click="deviceMode = 'simulation'"
          >
            <div class="mode-icon">S</div>
            <div class="mode-info">
              <div class="mode-label">Simulation</div>
              <div class="mode-desc">S5-HES behavioral simulation with 118+ device types</div>
            </div>
          </div>
          <div
            class="mode-option"
            :class="{ active: deviceMode === 'real' }"
            @click="deviceMode = 'real'"
          >
            <div class="mode-icon" style="background:rgba(52,211,153,0.15);color:var(--success)">R</div>
            <div class="mode-info">
              <div class="mode-label">Real</div>
              <div class="mode-desc">Physical IoT devices via MQTT / HTTP adapters</div>
            </div>
          </div>
          <div
            class="mode-option"
            :class="{ active: deviceMode === 'hybrid' }"
            @click="deviceMode = 'hybrid'"
          >
            <div class="mode-icon" style="background:rgba(251,191,36,0.15);color:var(--warning)">H</div>
            <div class="mode-info">
              <div class="mode-label">Hybrid</div>
              <div class="mode-desc">Mix simulated + real devices — test threats on real hardware</div>
            </div>
          </div>
        </div>
      </div>

      <!-- Real Device Configuration -->
      <div v-if="showRealDeviceConfig" class="real-device-config">
        <div class="flex-between" style="margin-bottom:10px">
          <div>
            <span style="font-size:13px;font-weight:600">Real Device Connections</span>
            <span class="text-muted" style="font-size:11px;margin-left:8px">
              {{ deviceMode === 'hybrid' ? 'Configure devices to replace simulated counterparts' : 'Add your physical IoT devices' }}
            </span>
          </div>
          <button class="btn btn-sm btn-primary" @click="addRealDevice">+ Add Device</button>
        </div>

        <div v-if="realDevices.length === 0" class="empty-state" style="padding:20px">
          <div style="font-size:11px">No real devices configured.
            <span v-if="deviceMode === 'real'" class="text-warning"> At least one device is required for real mode.</span>
          </div>
        </div>

        <div v-for="(dev, i) in realDevices" :key="i" class="real-device-card">
          <div class="flex-between" style="margin-bottom:8px">
            <span class="mono" style="font-size:12px;font-weight:600">{{ dev.device_id }}</span>
            <div class="flex gap-8">
              <button
                class="btn btn-sm btn-secondary"
                @click="testConnection(i)"
                :disabled="testingDevice === dev.device_id"
              >
                {{ testingDevice === dev.device_id ? 'Testing...' : 'Test' }}
              </button>
              <button class="btn btn-sm btn-danger" @click="removeRealDevice(i)">Remove</button>
            </div>
          </div>

          <!-- Test Result -->
          <div v-if="testResults[dev.device_id]" class="test-result" :class="testResults[dev.device_id].ok ? 'test-ok' : 'test-fail'">
            <span>{{ testResults[dev.device_id].ok ? 'Connected' : 'Failed' }}</span>
            <span v-if="testResults[dev.device_id].msg" class="text-muted" style="font-size:11px;margin-left:8px">
              {{ testResults[dev.device_id].msg }}
            </span>
            <span v-if="testResults[dev.device_id].latency_ms" class="text-muted" style="font-size:11px;margin-left:4px">
              ({{ testResults[dev.device_id].latency_ms }}ms)
            </span>
          </div>

          <div class="form-row">
            <div class="form-group">
              <label>Device ID</label>
              <input class="form-input" v-model="dev.device_id" placeholder="thermostat-001" />
            </div>
            <div class="form-group">
              <label>Device Type</label>
              <div v-if="customTypeActive[i]" class="custom-type-row">
                <input
                  class="form-input"
                  v-model="dev.device_type"
                  placeholder="e.g. smart_blinds"
                  style="flex:1"
                />
                <button
                  class="btn btn-sm btn-secondary"
                  title="Back to presets"
                  @click="customTypeActive[i] = false; dev.device_type = 'thermostat'"
                >Presets</button>
              </div>
              <select
                v-else
                class="form-select"
                :value="dev.device_type"
                @change="(e: any) => {
                  const val = e.target.value;
                  if (val === 'custom') {
                    customTypeActive[i] = true;
                    dev.device_type = '';
                  } else {
                    dev.device_type = val;
                  }
                }"
              >
                <option v-for="t in DEVICE_TYPES" :key="t" :value="t">
                  {{ t === 'custom' ? '+ Custom type...' : t }}
                </option>
              </select>
            </div>
          </div>

          <div class="form-row">
            <div class="form-group">
              <label>Protocol</label>
              <select class="form-select" v-model="dev.protocol">
                <option v-for="p in protocols" :key="p" :value="p">{{ p }}</option>
              </select>
            </div>
            <div class="form-group">
              <label>Room</label>
              <input class="form-input" v-model="dev.room" placeholder="living_room" />
            </div>
          </div>

          <div class="form-row">
            <div class="form-group">
              <label>Host</label>
              <input class="form-input" v-model="dev.host" placeholder="192.168.1.100" />
            </div>
            <div class="form-group">
              <label>Port</label>
              <input class="form-input" type="number" v-model.number="dev.port" />
            </div>
          </div>

          <div class="form-row" v-if="dev.protocol === 'mqtt'">
            <div class="form-group" style="grid-column:1/-1">
              <label>MQTT Topic</label>
              <input class="form-input" v-model="dev.topic" placeholder="home/living_room/thermostat" />
            </div>
          </div>

          <div class="form-row" v-if="dev.protocol === 'http'">
            <div class="form-group" style="grid-column:1/-1">
              <label>HTTP Endpoint</label>
              <input class="form-input" v-model="dev.endpoint" placeholder="/api/v1/device/status" />
            </div>
          </div>
        </div>
      </div>

      <div class="flex gap-8" style="margin-top:12px">
        <button class="btn btn-primary btn-sm" @click="createSession" :disabled="!canCreate || loading">
          {{ loading ? 'Creating...' : 'Create Session' }}
        </button>
        <button class="btn btn-secondary btn-sm" @click="showCreate = false">Cancel</button>
      </div>
    </div>

    <!-- Active Session -->
    <div v-if="session.active" class="card" style="border-color:var(--accent)">
      <div class="card-header">
        <div class="flex gap-8" style="align-items:center">
          <h3>Active: {{ session.sessionName }}</h3>
          <span class="badge badge-success">Active</span>
          <span
            v-if="session.deviceMode"
            class="badge"
            :class="{
              'badge-info': session.deviceMode === 'simulation',
              'badge-success': session.deviceMode === 'real',
              'badge-warning': session.deviceMode === 'hybrid',
            }"
          >
            {{ session.deviceMode }}
          </span>
        </div>
      </div>
      <div class="stat-grid">
        <div class="stat-card">
          <div class="label">Devices</div>
          <div class="value">{{ session.devices }}</div>
        </div>
        <div class="stat-card">
          <div class="label">Agents</div>
          <div class="value">{{ session.agents }}</div>
        </div>
        <div class="stat-card">
          <div class="label">Blockchain Blocks</div>
          <div class="value">{{ session.blockchainBlocks }}</div>
        </div>
        <div class="stat-card">
          <div class="label">Subsystems</div>
          <div class="value">{{ session.subsystemsReady }}/{{ session.subsystemsTotal }}</div>
        </div>
        <div v-if="session.deviceMode === 'hybrid' || session.deviceMode === 'real'" class="stat-card">
          <div class="label">Real Devices</div>
          <div class="value text-success">{{ session.realDevices }}</div>
        </div>
        <div v-if="session.deviceMode === 'hybrid'" class="stat-card">
          <div class="label">Simulated Devices</div>
          <div class="value text-info">{{ session.simulatedDevices }}</div>
        </div>
      </div>
    </div>

    <!-- Saved Sessions -->
    <div class="card">
      <div class="card-header">
        <h3>Saved Sessions</h3>
        <button class="btn btn-secondary btn-sm" @click="loadSessions" :disabled="loading">Refresh</button>
      </div>
      <div v-if="loading" class="empty-state"><div class="spinner"></div></div>
      <table v-else-if="sessions.length" class="data-table">
        <thead><tr><th>Name</th><th>Status</th><th>Mode</th><th>Created</th><th>Actions</th></tr></thead>
        <tbody>
          <tr v-for="s in sessions" :key="s.name">
            <td class="mono">{{ s.name }}</td>
            <td>
              <span class="badge" :class="s.active ? 'badge-success' : 'badge-neutral'">
                {{ s.active ? 'Active' : 'Saved' }}
              </span>
            </td>
            <td>
              <span
                v-if="s.device_mode"
                class="badge"
                :class="{
                  'badge-info': s.device_mode === 'simulation',
                  'badge-success': s.device_mode === 'real',
                  'badge-warning': s.device_mode === 'hybrid',
                }"
              >
                {{ s.device_mode }}
              </span>
              <span v-else class="text-muted" style="font-size:11px">—</span>
            </td>
            <td class="text-muted" style="font-size:12px">{{ s.created_iso ? new Date(s.created_iso).toLocaleString() : '—' }}</td>
            <td>
              <div class="flex gap-8">
                <button v-if="!s.active" class="btn btn-sm btn-primary" @click="resumeSess(s.name)">Resume</button>
                <button class="btn btn-sm btn-secondary" @click="saveSess(s.name)">Save</button>
                <button class="btn btn-sm btn-danger" @click="deleteSess(s.name)">Delete</button>
              </div>
            </td>
          </tr>
        </tbody>
      </table>
      <div v-else class="empty-state">No saved sessions yet.</div>
    </div>
  </div>
</template>

<style scoped>
/* ── Device Mode Selector ── */
.mode-selector {
  display: grid;
  grid-template-columns: 1fr 1fr 1fr;
  gap: 8px;
}
.mode-option {
  display: flex;
  align-items: flex-start;
  gap: 10px;
  padding: 12px;
  border-radius: var(--radius-sm);
  border: 2px solid var(--border);
  background: var(--bg-input);
  cursor: pointer;
  transition: all 0.15s;
}
.mode-option:hover {
  border-color: var(--border-hover);
  background: rgba(255,255,255,0.03);
}
.mode-option.active {
  border-color: var(--accent);
  background: var(--accent-glow);
}
.mode-icon {
  width: 32px;
  height: 32px;
  display: flex;
  align-items: center;
  justify-content: center;
  border-radius: 6px;
  background: var(--accent-glow);
  color: var(--accent);
  font-size: 14px;
  font-weight: 700;
  flex-shrink: 0;
}
.mode-info {
  display: flex;
  flex-direction: column;
  gap: 2px;
}
.mode-label {
  font-size: 13px;
  font-weight: 600;
  color: var(--text-primary);
}
.mode-desc {
  font-size: 11px;
  color: var(--text-muted);
  line-height: 1.3;
}

/* ── Real Device Config ── */
.real-device-config {
  margin-top: 12px;
  padding-top: 12px;
  border-top: 1px solid var(--border);
}
.real-device-card {
  background: var(--bg-input);
  border: 1px solid var(--border);
  border-radius: var(--radius-sm);
  padding: 12px;
  margin-bottom: 8px;
}

/* ── Test Result ── */
.test-result {
  padding: 4px 8px;
  border-radius: var(--radius-sm);
  font-size: 12px;
  font-weight: 500;
  margin-bottom: 8px;
}
.test-ok {
  background: rgba(52,211,153,0.15);
  color: var(--success);
}
.test-fail {
  background: rgba(248,113,113,0.15);
  color: var(--danger);
}

/* ── Custom Type Input ── */
.custom-type-row {
  display: flex;
  gap: 6px;
  align-items: center;
}

/* ── Responsive ── */
@media (max-width: 800px) {
  .mode-selector {
    grid-template-columns: 1fr;
  }
}
</style>
