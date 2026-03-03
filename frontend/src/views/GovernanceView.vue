<script setup lang="ts">
import { ref, onMounted, watch } from 'vue'
import { useWebSocket } from '@/composables/useWebSocket'
import * as api from '@/services/apiClient'

const tab = ref<'preferences' | 'models' | 'presets' | 'cost' | 'log'>('preferences')
const loading = ref(false)
const error = ref('')
const success = ref('')

interface PrefItem {
  key: string
  value: any
  tier: number
  editable: boolean
  validation: {
    type: string
    choices?: string[]
    min?: number
    max?: number
  } | null
}

const preferences = ref<PrefItem[]>([])
const lockedParams = ref<string[]>([])
const modelAssignments = ref<any[]>([])
const modelRegistry = ref<any[]>([])
const presets = ref<any[]>([])
const costTracking = ref<any>(null)
const governanceLog = ref<any[]>([])
const offchainStats = ref<any>(null)

// Live updates
const { data: wsData, connected: wsConnected } = useWebSocket('governance')
watch(wsData, () => { loadPreferences() })

async function loadPreferences() {
  try {
    const [pResp, lResp] = await Promise.all([api.getPreferences(), api.getLockedParams()])
    const raw = pResp.data?.preferences || pResp.data
    if (raw && typeof raw === 'object' && !Array.isArray(raw)) {
      preferences.value = Object.entries(raw).map(([k, v]: [string, any]) => ({
        key: k,
        value: v?.value ?? v,
        tier: v?.tier ?? 0,
        editable: v?.editable ?? true,
        validation: v?.validation ?? null,
      }))
    } else {
      preferences.value = Array.isArray(raw) ? raw : []
    }
    const lockData = lResp.data?.locked_parameters || lResp.data?.locked || lResp.data
    lockedParams.value = Array.isArray(lockData) ? lockData : Object.keys(lockData || {})
  } catch { /* ignore */ }
}

async function loadModels() {
  loading.value = true
  try {
    const [aResp, rResp] = await Promise.all([api.getModelAssignments(), api.getModelRegistry()])
    modelAssignments.value = Array.isArray(aResp.data) ? aResp.data : Object.entries(aResp.data).map(([k, v]) => ({ agent_id: k, ...(typeof v === 'object' ? v : { model: v }) as any }))
    modelRegistry.value = Array.isArray(rResp.data) ? rResp.data : (rResp.data.models || [])
  } catch { /* ignore */ }
  loading.value = false
}

async function loadPresets() {
  try {
    const resp = await api.getPresets()
    presets.value = Array.isArray(resp.data) ? resp.data : (resp.data.presets || [])
  } catch { /* ignore */ }
}

async function loadCost() {
  try {
    const resp = await api.getCostTracking()
    costTracking.value = resp.data
  } catch { /* ignore */ }
}

async function loadLog() {
  try {
    const resp = await api.getGovernanceLog()
    governanceLog.value = Array.isArray(resp.data) ? resp.data : (resp.data.log || resp.data.entries || resp.data.changes || [])
  } catch { /* ignore */ }
}

async function loadOffchain() {
  try {
    const resp = await api.getOffchainStats()
    offchainStats.value = resp.data
  } catch { /* ignore */ }
}

async function updatePref(key: string, value: any) {
  error.value = ''
  try {
    await api.updatePreference(key, { value })
    success.value = `Updated ${key}`
    setTimeout(() => success.value = '', 2000)
    await loadPreferences()
  } catch (e: any) {
    error.value = e.response?.data?.detail || 'Failed to update'
  }
}

async function applyPreset(name: string) {
  if (!confirm(`Apply preset "${name}"? This will change governance parameters.`)) return
  loading.value = true
  error.value = ''
  try {
    await api.applyPreset(name)
    success.value = `Applied preset: ${name}`
    setTimeout(() => success.value = '', 3000)
    await loadPreferences()
  } catch (e: any) {
    error.value = e.response?.data?.detail || 'Failed to apply preset'
  }
  loading.value = false
}

function switchTab(t: typeof tab.value) {
  tab.value = t
  if (t === 'preferences') loadPreferences()
  else if (t === 'models') loadModels()
  else if (t === 'presets') loadPresets()
  else if (t === 'cost') { loadCost(); loadOffchain() }
  else if (t === 'log') loadLog()
}

onMounted(() => {
  loadPreferences()
})
</script>

<template>
  <div>
    <div class="flex-between mb-16">
      <h1 class="page-title" style="margin-bottom:0">Governance</h1>
      <span class="badge" :class="wsConnected ? 'badge-success' : 'badge-neutral'">
        WS {{ wsConnected ? 'Live' : 'Off' }}
      </span>
    </div>

    <div v-if="error" class="card" style="border-color:var(--danger);margin-bottom:12px">
      <span class="text-danger">{{ error }}</span>
      <button class="btn btn-sm btn-secondary" style="margin-left:12px" @click="error=''">Dismiss</button>
    </div>
    <div v-if="success" class="card" style="border-color:var(--success);margin-bottom:12px">
      <span class="text-success">{{ success }}</span>
    </div>

    <div class="tab-bar">
      <div class="tab" :class="{ active: tab === 'preferences' }" @click="switchTab('preferences')">Preferences</div>
      <div class="tab" :class="{ active: tab === 'models' }" @click="switchTab('models')">Models</div>
      <div class="tab" :class="{ active: tab === 'presets' }" @click="switchTab('presets')">Presets</div>
      <div class="tab" :class="{ active: tab === 'cost' }" @click="switchTab('cost')">Cost</div>
      <div class="tab" :class="{ active: tab === 'log' }" @click="switchTab('log')">Log</div>
    </div>

    <!-- Preferences Tab -->
    <div v-if="tab === 'preferences'">
      <div class="card">
        <div class="card-header">
          <h3>User Preferences</h3>
          <button class="btn btn-secondary btn-sm" @click="loadPreferences">Refresh</button>
        </div>
        <table v-if="preferences.length" class="data-table">
          <thead><tr><th>Key</th><th>Tier</th><th>Value</th><th>Status</th></tr></thead>
          <tbody>
            <tr v-for="p in preferences" :key="p.key">
              <td class="mono" style="font-size:12px">{{ p.key }}</td>
              <td>
                <span class="tier-badge" :class="{
                  'tier-1': p.tier === 1,
                  'tier-2': p.tier === 2,
                  'tier-3': p.tier === 3,
                  'tier-4': p.tier === 4,
                }">T{{ p.tier }}</span>
              </td>
              <td>
                <!-- Choice-type: dropdown -->
                <select v-if="p.validation?.choices"
                  class="form-input" style="width:210px;display:inline-block"
                  :value="p.value"
                  @change="(e: Event) => updatePref(p.key, (e.target as HTMLSelectElement).value)"
                  :disabled="!p.editable">
                  <option v-for="c in p.validation.choices" :key="c" :value="c">{{ c }}</option>
                </select>

                <!-- Boolean: checkbox -->
                <label v-else-if="typeof p.value === 'boolean'"
                  style="display:inline-flex;align-items:center;gap:6px;cursor:pointer">
                  <input type="checkbox"
                    :checked="p.value"
                    @change="(e: Event) => updatePref(p.key, (e.target as HTMLInputElement).checked)"
                    :disabled="!p.editable" />
                  {{ p.value ? 'On' : 'Off' }}
                </label>

                <!-- Numeric with min/max: number input -->
                <input v-else-if="p.validation?.min != null || p.validation?.max != null"
                  class="form-input" style="width:210px;display:inline-block"
                  type="number"
                  :value="p.value"
                  :min="p.validation?.min"
                  :max="p.validation?.max"
                  :step="p.validation?.type === 'int' ? 1 : 0.1"
                  @change="(e: Event) => {
                    const raw = (e.target as HTMLInputElement).value;
                    const val = p.validation?.type === 'int' ? parseInt(raw) : parseFloat(raw);
                    updatePref(p.key, val);
                  }"
                  :disabled="!p.editable" />

                <!-- Object / array: read-only JSON -->
                <span v-else-if="typeof p.value === 'object'" class="mono text-muted" style="font-size:11px">
                  {{ JSON.stringify(p.value) }}
                </span>

                <!-- Default: text input -->
                <input v-else
                  class="form-input" style="width:210px;display:inline-block"
                  :value="p.value"
                  @change="(e: Event) => updatePref(p.key, (e.target as HTMLInputElement).value)"
                  :disabled="!p.editable" />
              </td>
              <td>
                <span v-if="!p.editable" class="badge badge-warning">Locked</span>
              </td>
            </tr>
          </tbody>
        </table>
        <div v-else class="empty-state">No preferences configured.</div>
      </div>
    </div>

    <!-- Models Tab -->
    <div v-if="tab === 'models'">
      <div class="card">
        <div class="card-header"><h3>Model Assignments</h3></div>
        <table v-if="modelAssignments.length" class="data-table">
          <thead><tr><th>Agent</th><th>Model</th><th>Provider</th></tr></thead>
          <tbody>
            <tr v-for="m in modelAssignments" :key="m.agent_id">
              <td class="mono" style="font-size:12px">{{ m.agent_id }}</td>
              <td>{{ m.model || m.model_id || '—' }}</td>
              <td class="text-muted">{{ m.provider || '—' }}</td>
            </tr>
          </tbody>
        </table>
        <div v-else class="empty-state">No model assignments.</div>
      </div>
      <div class="card" v-if="modelRegistry.length">
        <div class="card-header"><h3>Model Registry</h3></div>
        <table class="data-table">
          <thead><tr><th>Model</th><th>Provider</th><th>Cost / 1K</th><th>Capabilities</th></tr></thead>
          <tbody>
            <tr v-for="m in modelRegistry" :key="m.id || m.name">
              <td>{{ m.name || m.id }}</td>
              <td>{{ m.provider || '—' }}</td>
              <td>{{ m.cost_per_1k != null ? `$${m.cost_per_1k}` : '—' }}</td>
              <td>
                <span v-for="c in (m.capabilities || [])" :key="c" class="badge badge-info" style="margin-right:4px;font-size:10px">{{ c }}</span>
              </td>
            </tr>
          </tbody>
        </table>
      </div>
    </div>

    <!-- Presets Tab -->
    <div v-if="tab === 'presets'" class="card">
      <div class="card-header"><h3>Governance Presets</h3></div>
      <div v-if="presets.length" style="display:grid;grid-template-columns:repeat(auto-fill,minmax(250px,1fr));gap:12px">
        <div v-for="p in presets" :key="p.name" class="stat-card" style="cursor:pointer" @click="applyPreset(p.name)">
          <div style="font-weight:600;margin-bottom:4px">{{ p.name }}</div>
          <div class="text-muted" style="font-size:12px">{{ p.description || 'No description' }}</div>
          <button class="btn btn-primary btn-sm" style="margin-top:8px" :disabled="loading">Apply</button>
        </div>
      </div>
      <div v-else class="empty-state">No presets available.</div>
    </div>

    <!-- Cost Tab -->
    <div v-if="tab === 'cost'">
      <div v-if="costTracking" class="card">
        <div class="card-header"><h3>Cost Tracking</h3></div>
        <div class="stat-grid">
          <div class="stat-card">
            <div class="label">Total Cost</div>
            <div class="value">${{ costTracking.total_cost?.toFixed(4) ?? '0.00' }}</div>
          </div>
          <div class="stat-card">
            <div class="label">API Calls</div>
            <div class="value">{{ costTracking.total_calls ?? costTracking.api_calls ?? 0 }}</div>
          </div>
          <div class="stat-card">
            <div class="label">Tokens Used</div>
            <div class="value">{{ costTracking.total_tokens ?? 0 }}</div>
          </div>
        </div>
        <div v-if="costTracking.by_agent" style="margin-top:12px">
          <strong style="font-size:13px">Cost by Agent</strong>
          <table class="data-table" style="margin-top:8px">
            <thead><tr><th>Agent</th><th>Calls</th><th>Cost</th></tr></thead>
            <tbody>
              <tr v-for="(data, agentId) in costTracking.by_agent" :key="agentId as string">
                <td class="mono" style="font-size:12px">{{ agentId }}</td>
                <td>{{ (data as any).calls ?? 0 }}</td>
                <td>${{ (data as any).cost?.toFixed(4) ?? '0.00' }}</td>
              </tr>
            </tbody>
          </table>
        </div>
      </div>
      <div v-if="offchainStats" class="card">
        <div class="card-header"><h3>Off-chain Storage</h3></div>
        <div class="stat-grid">
          <div class="stat-card">
            <div class="label">Records</div>
            <div class="value">{{ offchainStats.total_records ?? offchainStats.records ?? 0 }}</div>
          </div>
          <div class="stat-card">
            <div class="label">Storage Size</div>
            <div class="value">{{ offchainStats.storage_size || offchainStats.size || '—' }}</div>
          </div>
        </div>
      </div>
    </div>

    <!-- Log Tab -->
    <div v-if="tab === 'log'" class="card">
      <div class="card-header">
        <h3>Governance Log</h3>
        <button class="btn btn-secondary btn-sm" @click="loadLog">Refresh</button>
      </div>
      <div v-if="governanceLog.length" style="max-height:500px;overflow-y:auto">
        <table class="data-table">
          <thead><tr><th>Time</th><th>Action</th><th>Agent</th><th>Details</th></tr></thead>
          <tbody>
            <tr v-for="(entry, i) in governanceLog" :key="i">
              <td class="mono text-muted" style="font-size:11px;white-space:nowrap">{{ entry.timestamp || '—' }}</td>
              <td><span class="badge badge-info">{{ entry.action || entry.type || '—' }}</span></td>
              <td class="mono" style="font-size:12px">{{ entry.agent_id || '—' }}</td>
              <td class="text-muted" style="font-size:12px">{{ entry.details || entry.message || '—' }}</td>
            </tr>
          </tbody>
        </table>
      </div>
      <div v-else class="empty-state">No governance log entries.</div>
    </div>
  </div>
</template>

<style scoped>
.tier-badge {
  display: inline-block;
  padding: 2px 8px;
  border-radius: 4px;
  font-size: 11px;
  font-weight: 600;
  letter-spacing: 0.5px;
}
.tier-1 { background: rgba(76, 175, 80, 0.15); color: #4caf50; }
.tier-2 { background: rgba(33, 150, 243, 0.15); color: #2196f3; }
.tier-3 { background: rgba(255, 152, 0, 0.15); color: #ff9800; }
.tier-4 { background: rgba(244, 67, 54, 0.15); color: #f44336; }
</style>
