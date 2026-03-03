<script setup lang="ts">
import { ref, computed, watch, onMounted, nextTick } from 'vue'
import { useWebSocket } from '@/composables/useWebSocket'
import * as api from '@/services/apiClient'

// ── Types ──
interface AgentInfo {
  agent_id: string; role: string; priority: number; model: string
  description: string; allowed_device_types: string[]
  is_llm_agent: boolean; is_specialized: boolean
}
interface AgentLiveState {
  status: 'idle' | 'reasoning' | 'decided'
  lastDecision: FeedEntry | null
  decisionCount: number; acceptedCount: number
}
interface FeedEntry {
  id: string; timestamp: string
  type: 'decision' | 'conflict' | 'cycle' | 'session' | 'anomaly' | 'anomaly_trained' | 'arbitration'
  agentId?: string; agentRole?: string
  action?: string; targetDevice?: string
  confidence?: number; accepted?: boolean; executed?: boolean
  agentA?: string; agentB?: string; winner?: string; device?: string
  cycleLabel?: string; agentsRun?: number; decisions?: number
  errors?: number; blockMined?: number | null
  sessionName?: string
  // Anomaly fields
  deviceType?: string; anomalyScore?: number; detectorsTriggered?: string[]
  devicesProfiled?: number; totalSamples?: number
  // Arbitration fields
  winnerAgent?: string; winnerAction?: string; loserAgents?: string[]
  method?: string; reasoning?: string
}
interface AgentDecision {
  action: string; target_device: string; accepted: boolean
  conflict: boolean; conflict_winner: string
  confidence: number; reasoning_summary: string
}

// ── Role Config ──
const ROLE_CONFIG: Record<string, { icon: string; color: string; label: string }> = {
  safety:      { icon: '🛡️', color: '#ef4444', label: 'Safety' },
  health:      { icon: '💊', color: '#22c55e', label: 'Health' },
  security:    { icon: '🔒', color: '#f59e0b', label: 'Security' },
  privacy:     { icon: '👁️', color: '#8b5cf6', label: 'Privacy' },
  energy:      { icon: '⚡', color: '#3b82f6', label: 'Energy' },
  climate:     { icon: '🌡️', color: '#06b6d4', label: 'Climate' },
  maintenance: { icon: '🔧', color: '#78716c', label: 'Maintenance' },
  nlu:         { icon: '💬', color: '#ec4899', label: 'NLU' },
  anomaly:     { icon: '📊', color: '#f97316', label: 'Anomaly' },
  arbitration: { icon: '⚖️', color: '#a855f7', label: 'Arbitration' },
}

function getRoleConfig(role: string) {
  return ROLE_CONFIG[role] || { icon: '🤖', color: '#6b7280', label: role }
}
function agentShortName(agentId: string): string {
  const role = agentId.split('-')[0]
  return ROLE_CONFIG[role]?.label || role
}
function formatTimestamp(ts: string): string {
  try { return new Date(ts).toLocaleTimeString('en-US', { hour12: false, hour: '2-digit', minute: '2-digit', second: '2-digit' }) }
  catch { return ts || '—' }
}
function formatPercent(value: number): string {
  return (value * 100).toFixed(0) + '%'
}
function genId(): string {
  return `f-${Date.now()}-${Math.random().toString(36).slice(2, 6)}`
}

// ── State ──
const agents = ref<AgentInfo[]>([])
const selectedAgentId = ref<string | null>(null)
const agentDetail = ref<any>(null)
const agentDecisions = ref<AgentDecision[]>([])
const detailLoading = ref(false)
const showDetailOverlay = ref(false)

const agentStates = ref<Record<string, AgentLiveState>>({})
const activityFeed = ref<FeedEntry[]>([])
const feedFilter = ref<'all' | 'decision' | 'conflict' | 'cycle'>('all')

const totalCycles = ref(0)
const totalDecisions = ref(0)
const totalAccepted = ref(0)
const totalConflicts = ref(0)
const lastBlockIndex = ref<number | null>(null)
const currentCycleLabel = ref('')

const loading = ref(false)
const cycleLoading = ref(false)
const error = ref('')
const feedRef = ref<HTMLDivElement | null>(null)

// Run configuration (shared across all run modes)
const runIntervalSec = ref(60)
const runDurationMin = ref(5)

// Auto-run state
const autoRunActive = ref(false)
const autoRunCycles = ref(0)
const autoRunRemaining = ref(0)
let remainingTimer: ReturnType<typeof setInterval> | null = null

// ── WebSocket ──
const { data: wsData, connected: wsConnected } = useWebSocket('agents')

watch(wsData, (val) => {
  if (!val || !val.event_type) return
  switch (val.event_type) {
    case 'agent_roster': handleRoster(val); break
    case 'agent_decision': handleDecision(val); break
    case 'agent_conflict': handleConflict(val); break
    case 'auto_cycle_complete': handleCycleComplete(val); break
    case 'session_auto_created': handleSessionCreated(val); break
    case 'anomaly_detected': handleAnomalyDetected(val); break
    case 'anomaly_trained': handleAnomalyTrained(val); break
    case 'arbitration_resolved': handleArbitrationResolved(val); break
    case 'auto_run_stopped': handleAutoRunStopped(val); break
  }
})

function initAgentState(agentId: string) {
  if (!agentStates.value[agentId]) {
    agentStates.value[agentId] = { status: 'idle', lastDecision: null, decisionCount: 0, acceptedCount: 0 }
  }
}

function addFeedEntry(entry: FeedEntry) {
  activityFeed.value.unshift(entry)
  if (activityFeed.value.length > 200) activityFeed.value.pop()
  nextTick(() => { if (feedRef.value) feedRef.value.scrollTop = 0 })
}

function handleRoster(data: any) {
  for (const a of (data.agents || [])) initAgentState(a.agent_id)
}

function handleDecision(data: any) {
  const entry: FeedEntry = {
    id: genId(), timestamp: data.timestamp || new Date().toISOString(),
    type: 'decision', agentId: data.agent_id,
    agentRole: agents.value.find(a => a.agent_id === data.agent_id)?.role || data.agent_id.split('-')[0],
    action: data.action, targetDevice: data.target_device,
    confidence: data.confidence, accepted: data.accepted, executed: data.executed,
  }
  addFeedEntry(entry)
  totalDecisions.value++
  if (data.accepted) totalAccepted.value++

  const st = agentStates.value[data.agent_id]
  if (st) {
    st.status = 'decided'
    st.lastDecision = entry
    st.decisionCount++
    if (data.accepted) st.acceptedCount++
  }
  if (data.agent_id === selectedAgentId.value) refreshSelectedDetail()
}

function handleConflict(data: any) {
  addFeedEntry({
    id: genId(), timestamp: data.timestamp || new Date().toISOString(),
    type: 'conflict', agentA: data.agent_a, agentB: data.agent_b,
    winner: data.winner, device: data.device,
  })
  totalConflicts.value++
}

function handleCycleComplete(data: any) {
  addFeedEntry({
    id: genId(), timestamp: data.timestamp || new Date().toISOString(),
    type: 'cycle', cycleLabel: data.cycle_label, agentsRun: data.agents_run,
    decisions: data.decisions, errors: data.errors, blockMined: data.block_mined,
  })
  totalCycles.value++
  autoRunCycles.value = parseInt(data.cycle_label) || autoRunCycles.value + 1
  currentCycleLabel.value = data.cycle_label || ''
  if (data.block_mined != null) lastBlockIndex.value = data.block_mined
  for (const a of agents.value) {
    if (agentStates.value[a.agent_id]) {
      agentStates.value[a.agent_id].status = 'idle'
    }
  }
}

function handleSessionCreated(data: any) {
  addFeedEntry({
    id: genId(), timestamp: data.timestamp || new Date().toISOString(),
    type: 'session', sessionName: data.session_name,
  })
  loadAgents()
}

function handleAnomalyDetected(data: any) {
  addFeedEntry({
    id: genId(), timestamp: data.timestamp || new Date().toISOString(),
    type: 'anomaly', agentId: data.agent_id,
    device: data.device_id, deviceType: data.device_type,
    anomalyScore: data.anomaly_score,
    detectorsTriggered: data.detectors_triggered || [],
  })
  const st = agentStates.value['anomaly-agent-009']
  if (st) { st.status = 'decided'; st.decisionCount++ }
}

function handleAnomalyTrained(data: any) {
  addFeedEntry({
    id: genId(), timestamp: data.timestamp || new Date().toISOString(),
    type: 'anomaly_trained', agentId: data.agent_id,
    devicesProfiled: data.devices_profiled, totalSamples: data.total_samples,
  })
}

function handleArbitrationResolved(data: any) {
  addFeedEntry({
    id: genId(), timestamp: data.timestamp || new Date().toISOString(),
    type: 'arbitration', agentId: data.agent_id,
    device: data.device, winnerAgent: data.winner_agent,
    winnerAction: data.winner_action, loserAgents: data.loser_agents || [],
    method: data.method, reasoning: data.reasoning,
    confidence: data.confidence,
  })
  totalConflicts.value++
  const st = agentStates.value['arbitration-agent-010']
  if (st) { st.status = 'decided'; st.decisionCount++ }
}

function handleAutoRunStopped(data: any) {
  autoRunActive.value = false
  autoRunRemaining.value = 0
  autoRunCycles.value = data.cycles_completed || autoRunCycles.value
  stopRemainingTimer()
  addFeedEntry({
    id: genId(), timestamp: data.timestamp || new Date().toISOString(),
    type: 'cycle', cycleLabel: 'Auto-run completed',
    agentsRun: 0, decisions: 0, errors: 0, blockMined: null,
  })
}

// ── Computed ──
const llmAgents = computed(() => agents.value.filter(a => a.is_llm_agent))
const specializedAgents = computed(() => agents.value.filter(a => a.is_specialized))
const filteredFeed = computed(() => {
  if (feedFilter.value === 'all') return activityFeed.value
  return activityFeed.value.filter(e => e.type === feedFilter.value)
})
const overallAcceptanceRate = computed(() => {
  if (totalDecisions.value === 0) return 0
  return totalAccepted.value / totalDecisions.value
})
const selectedAgent = computed(() => {
  if (!selectedAgentId.value) return null
  return agents.value.find(a => a.agent_id === selectedAgentId.value) || null
})
const selectedAgentState = computed(() => {
  if (!selectedAgentId.value) return null
  return agentStates.value[selectedAgentId.value] || null
})

// ── REST API ──
async function loadAgents() {
  try {
    const resp = await api.getAgents()
    agents.value = resp.data.agents || []
    for (const a of agents.value) initAgentState(a.agent_id)
  } catch { /* session may not be active */ }
}

async function selectAgent(agentId: string) {
  selectedAgentId.value = agentId
  showDetailOverlay.value = true
  await refreshSelectedDetail()
}

async function refreshSelectedDetail() {
  if (!selectedAgentId.value) return
  detailLoading.value = true
  error.value = ''
  try {
    const [aResp, dResp] = await Promise.all([
      api.getAgent(selectedAgentId.value),
      api.getAgentDecisions(selectedAgentId.value),
    ])
    agentDetail.value = aResp.data
    agentDecisions.value = dResp.data.decisions || []
  } catch (e: any) {
    // REST failed — build detail from the already-loaded agents list
    const agent = agents.value.find(a => a.agent_id === selectedAgentId.value)
    if (agent) {
      agentDetail.value = {
        agent_id: agent.agent_id,
        role: agent.role,
        priority: agent.priority,
        model: agent.model,
        description: agent.description,
        allowed_device_types: agent.allowed_device_types,
        permissions: [],
        decision_stats: {},
      }
      agentDecisions.value = []
    }
  }
  detailLoading.value = false
}

function getDurationSec(): number {
  return Math.max(0, (runDurationMin.value || 0)) * 60
}
function getIntervalSec(): number {
  return Math.max(5, runIntervalSec.value || 60)
}

async function runParallel() {
  loading.value = true; error.value = ''
  for (const a of agents.value) {
    if (a.is_llm_agent && agentStates.value[a.agent_id]) {
      agentStates.value[a.agent_id].status = 'reasoning'
    }
  }
  try {
    const dur = getDurationSec()
    const intv = getIntervalSec()
    const resp = await api.runAgentsParallel(dur, intv)
    if (resp.data.status === 'started') {
      // Duration mode — backend started a background loop
      autoRunActive.value = true
      autoRunCycles.value = resp.data.cycles_completed || 0
      autoRunRemaining.value = dur
      startRemainingTimer()
    }
    await loadAgents()
  } catch (e: any) {
    error.value = e.response?.data?.detail || 'Failed to run parallel agents'
    for (const a of agents.value) {
      if (a.is_llm_agent && agentStates.value[a.agent_id]) {
        agentStates.value[a.agent_id].status = 'idle'
      }
    }
  }
  loading.value = false
}

async function runCycle() {
  cycleLoading.value = true; error.value = ''
  for (const a of agents.value) {
    if (a.is_llm_agent && agentStates.value[a.agent_id]) {
      agentStates.value[a.agent_id].status = 'reasoning'
    }
  }
  try {
    const dur = getDurationSec()
    const intv = getIntervalSec()
    const resp = await api.runAgentCycle(dur, intv)
    if (resp.data.status === 'started') {
      autoRunActive.value = true
      autoRunCycles.value = resp.data.cycles_completed || 0
      autoRunRemaining.value = dur
      startRemainingTimer()
    }
    await loadAgents()
  } catch (e: any) {
    error.value = e.response?.data?.detail || 'Failed to run agent cycle'
    for (const a of agents.value) {
      if (a.is_llm_agent && agentStates.value[a.agent_id]) {
        agentStates.value[a.agent_id].status = 'idle'
      }
    }
  }
  cycleLoading.value = false
}

async function startAutoRun() {
  error.value = ''
  const intv = getIntervalSec()
  const dur = getDurationSec()
  runIntervalSec.value = intv
  try {
    const resp = await api.startAutoRun(intv, dur)
    autoRunActive.value = true
    autoRunCycles.value = resp.data.cycles_completed || 0
    autoRunRemaining.value = dur
    if (dur > 0) startRemainingTimer()
  } catch (e: any) {
    error.value = e.response?.data?.detail || 'Failed to start auto-run'
  }
}

async function stopAutoRun() {
  error.value = ''
  stopRemainingTimer()
  try {
    const resp = await api.stopAutoRun()
    autoRunActive.value = false
    autoRunCycles.value = resp.data.cycles_completed || 0
    autoRunRemaining.value = 0
  } catch (e: any) {
    error.value = e.response?.data?.detail || 'Failed to stop auto-run'
  }
}

function startRemainingTimer() {
  stopRemainingTimer()
  remainingTimer = setInterval(async () => {
    if (!autoRunActive.value) { stopRemainingTimer(); return }
    try {
      const resp = await api.getAutoRunStatus()
      autoRunActive.value = resp.data.active
      autoRunCycles.value = resp.data.cycles_completed
      autoRunRemaining.value = resp.data.remaining || 0
      if (!resp.data.active) {
        autoRunRemaining.value = 0
        stopRemainingTimer()
      }
    } catch { /* ignore */ }
  }, 5000)
}

function stopRemainingTimer() {
  if (remainingTimer) { clearInterval(remainingTimer); remainingTimer = null }
}

function formatRemaining(sec: number): string {
  if (sec <= 0) return ''
  const m = Math.floor(sec / 60)
  const s = Math.floor(sec % 60)
  return m > 0 ? `${m}m ${s}s left` : `${s}s left`
}

async function loadAutoRunStatus() {
  try {
    const resp = await api.getAutoRunStatus()
    autoRunActive.value = resp.data.active
    runIntervalSec.value = resp.data.interval
    autoRunCycles.value = resp.data.cycles_completed
    autoRunRemaining.value = resp.data.remaining || 0
    if (resp.data.active && resp.data.duration > 0) startRemainingTimer()
  } catch { /* session may not be active */ }
}

function getAgentAccRate(agentId: string): string {
  const st = agentStates.value[agentId]
  if (!st || st.decisionCount === 0) return ''
  return formatPercent(st.acceptedCount / st.decisionCount)
}

async function loadActivityHistory() {
  try {
    const resp = await api.getActivityHistory(200)
    const data = resp.data
    // Restore counters
    totalDecisions.value = data.counters?.total_decisions || 0
    totalAccepted.value = data.counters?.total_accepted || 0
    totalConflicts.value = data.counters?.total_conflicts || 0

    // Restore feed entries (already sorted newest-first from backend)
    const restored: FeedEntry[] = []
    for (const e of (data.entries || [])) {
      const ts = typeof e.timestamp === 'number'
        ? new Date(e.timestamp * 1000).toISOString()
        : e.timestamp
      if (e.type === 'decision') {
        restored.push({
          id: genId(), timestamp: ts,
          type: 'decision', agentId: e.agentId, agentRole: e.agentRole,
          action: e.action, targetDevice: e.targetDevice,
          confidence: e.confidence, accepted: e.accepted, executed: e.executed,
        })
        // Update per-agent state counters
        if (e.agentId) {
          initAgentState(e.agentId)
          const st = agentStates.value[e.agentId]
          if (st) {
            st.decisionCount++
            if (e.accepted) st.acceptedCount++
          }
        }
      } else if (e.type === 'conflict') {
        restored.push({
          id: genId(), timestamp: ts,
          type: 'conflict', winner: e.winner, device: e.device,
        })
      }
    }
    activityFeed.value = restored
  } catch { /* session may not be active yet */ }
}

onMounted(() => { loadAgents(); loadAutoRunStatus(); loadActivityHistory() })
</script>

<template>
  <div class="agent-monitor">
    <!-- Header -->
    <div class="am-header">
      <div class="header-left">
        <h2 style="margin:0;font-size:18px">Agent Monitor</h2>
        <span class="badge" :class="wsConnected ? 'badge-success' : 'badge-neutral'" style="font-size:10px">
          WS {{ wsConnected ? 'Live' : 'Off' }}
        </span>
        <span v-if="currentCycleLabel" class="badge badge-info" style="font-size:10px;animation:pulse 2s infinite">
          Cycle {{ currentCycleLabel }}
        </span>
      </div>
      <div class="header-right">
        <!-- Timing Controls -->
        <div v-if="!autoRunActive" class="run-config">
          <div class="config-field">
            <label class="config-label">Interval</label>
            <div class="config-input-group">
              <input v-model.number="runIntervalSec" type="number" min="5" step="1"
                class="config-input" placeholder="60" />
              <span class="config-unit">sec</span>
            </div>
          </div>
          <div class="config-field">
            <label class="config-label">Duration</label>
            <div class="config-input-group">
              <input v-model.number="runDurationMin" type="number" min="0" step="1"
                class="config-input" placeholder="5" />
              <span class="config-unit">min</span>
            </div>
          </div>
          <span class="header-sep">|</span>
          <button class="btn btn-outline btn-sm" @click="runCycle" :disabled="cycleLoading || loading"
            title="Run agents sequentially for the set duration">
            {{ cycleLoading ? '...' : 'Cycle' }}
          </button>
          <button class="btn btn-primary btn-sm" @click="runParallel" :disabled="loading || cycleLoading"
            title="Run agents in parallel for the set duration">
            {{ loading ? '...' : 'Parallel' }}
          </button>
          <button class="btn btn-success btn-sm" @click="startAutoRun" :disabled="loading || cycleLoading"
            title="Start continuous auto-run for the set duration">
            Auto
          </button>
        </div>
        <!-- Running State -->
        <div v-else class="auto-run-controls">
          <span class="auto-run-indicator">
            <span class="auto-run-dot"></span>
            Running · Cycle {{ autoRunCycles }}
          </span>
          <span v-if="autoRunRemaining > 0" class="remaining-label">
            {{ formatRemaining(autoRunRemaining) }}
          </span>
          <button class="btn btn-danger btn-sm" @click="stopAutoRun">
            Stop
          </button>
        </div>
        <button class="btn btn-ghost btn-sm" @click="loadAgents">↻</button>
      </div>
    </div>

    <!-- Error -->
    <div v-if="error" class="error-bar">
      <span class="text-danger">{{ error }}</span>
      <button class="btn btn-ghost btn-sm" @click="error=''">Dismiss</button>
    </div>

    <!-- Stats Row -->
    <div class="stats-row">
      <div class="mini-stat">
        <span class="mini-stat-value">{{ totalCycles }}</span>
        <span class="mini-stat-label">Cycles</span>
      </div>
      <div class="mini-stat">
        <span class="mini-stat-value">{{ totalDecisions }}</span>
        <span class="mini-stat-label">Decisions</span>
      </div>
      <div class="mini-stat">
        <span class="mini-stat-value" style="color:#22c55e">{{ formatPercent(overallAcceptanceRate) }}</span>
        <span class="mini-stat-label">Accepted</span>
      </div>
      <div class="mini-stat">
        <span class="mini-stat-value" style="color:#f59e0b">{{ totalConflicts }}</span>
        <span class="mini-stat-label">Conflicts</span>
      </div>
      <div class="mini-stat">
        <span class="mini-stat-value">{{ lastBlockIndex ?? '—' }}</span>
        <span class="mini-stat-label">Last Block</span>
      </div>
    </div>

    <!-- 3-Column Content -->
    <div class="am-content">
      <!-- LEFT: Agent Cards -->
      <div class="am-agents-panel">
        <div class="panel-header">
          <span class="panel-title">Agents</span>
          <span class="panel-subtitle">{{ agents.length }} total</span>
        </div>
        <div class="am-agent-list">
          <div v-if="!agents.length" class="empty-state" style="padding:20px">
            <div style="font-size:36px">🤖</div>
            <div style="font-size:13px;font-weight:500">No Agents</div>
            <div style="font-size:11px">Create a session or start a simulation</div>
          </div>

          <!-- LLM Agents -->
          <div v-if="llmAgents.length" class="agent-section-header">LLM Agents ({{ llmAgents.length }})</div>
          <div v-for="a in llmAgents" :key="a.agent_id"
            class="am-agent-card" :class="{ selected: selectedAgentId === a.agent_id }"
            @click="selectAgent(a.agent_id)">
            <div class="agent-card-top">
              <div class="agent-card-left">
                <span class="agent-icon">{{ getRoleConfig(a.role).icon }}</span>
                <div>
                  <div class="agent-card-name">{{ getRoleConfig(a.role).label }}</div>
                  <div class="agent-card-meta mono">{{ a.model }}</div>
                </div>
              </div>
              <div class="agent-card-right">
                <div class="agent-status-dot" :class="agentStates[a.agent_id]?.status || 'idle'"></div>
                <span class="badge badge-neutral" style="font-size:9px">P{{ a.priority }}</span>
              </div>
            </div>
            <div v-if="agentStates[a.agent_id]?.decisionCount > 0" class="agent-card-stats">
              <span>{{ agentStates[a.agent_id].decisionCount }} dec</span>
              <span>{{ getAgentAccRate(a.agent_id) }} acc</span>
            </div>
          </div>

          <!-- Specialized Agents -->
          <div v-if="specializedAgents.length" class="agent-section-header">Specialized ({{ specializedAgents.length }})</div>
          <div v-for="a in specializedAgents" :key="a.agent_id"
            class="am-agent-card" :class="{ selected: selectedAgentId === a.agent_id }"
            @click="selectAgent(a.agent_id)">
            <div class="agent-card-top">
              <div class="agent-card-left">
                <span class="agent-icon">{{ getRoleConfig(a.role).icon }}</span>
                <div>
                  <div class="agent-card-name">{{ getRoleConfig(a.role).label }}</div>
                  <div class="agent-card-meta mono">{{ a.model || 'n/a' }}</div>
                </div>
              </div>
              <div class="agent-card-right">
                <div class="agent-status-dot" :class="agentStates[a.agent_id]?.status || 'idle'"></div>
                <span class="badge badge-neutral" style="font-size:9px">P{{ a.priority }}</span>
              </div>
            </div>
          </div>
        </div>
      </div>

      <!-- CENTER: Activity Feed -->
      <div class="am-feed-panel">
        <div class="feed-header">
          <span class="panel-title">Activity Feed ({{ activityFeed.length }})</span>
          <div style="display:flex;gap:4px">
            <button v-for="f in [
              { key: 'all', label: 'All' },
              { key: 'decision', label: 'Decisions' },
              { key: 'conflict', label: 'Conflicts' },
              { key: 'cycle', label: 'Cycles' },
            ]" :key="f.key" class="filter-btn" :class="{ active: feedFilter === f.key }"
              @click="feedFilter = f.key as any">{{ f.label }}</button>
          </div>
        </div>
        <div ref="feedRef" class="am-feed-content">
          <div v-if="filteredFeed.length === 0" class="empty-state" style="padding:24px">
            <div style="font-size:36px">📡</div>
            <div style="font-size:13px;font-weight:500">No Activity</div>
            <div style="font-size:11px">Run a cycle or start a simulation to see live events</div>
          </div>

          <div v-for="entry in filteredFeed" :key="entry.id"
            class="feed-entry" :class="'feed-' + entry.type">

            <!-- Decision -->
            <template v-if="entry.type === 'decision'">
              <div class="feed-entry-header">
                <span class="agent-tag" :style="{ color: getRoleConfig(entry.agentRole || '').color }">
                  {{ getRoleConfig(entry.agentRole || '').icon }} {{ agentShortName(entry.agentId || '') }}
                </span>
                <span class="event-timestamp">{{ formatTimestamp(entry.timestamp) }}</span>
              </div>
              <div class="feed-entry-body">
                <span class="mono" style="font-size:11px">{{ entry.action }} → {{ entry.targetDevice }}</span>
                <div class="feed-entry-badges">
                  <div class="confidence-bar">
                    <div class="confidence-fill" :style="{ width: ((entry.confidence || 0) * 100) + '%' }"></div>
                  </div>
                  <span style="font-size:10px;color:var(--text-muted)">{{ ((entry.confidence || 0) * 100).toFixed(0) }}%</span>
                  <span class="badge" :class="entry.accepted ? 'badge-success' : 'badge-danger'" style="font-size:9px">
                    {{ entry.accepted ? 'Accepted' : 'Rejected' }}
                  </span>
                  <span v-if="entry.executed" class="badge badge-info" style="font-size:9px">Executed</span>
                </div>
              </div>
            </template>

            <!-- Conflict -->
            <template v-else-if="entry.type === 'conflict'">
              <div class="feed-entry-header">
                <span style="font-size:12px;font-weight:600;color:#f59e0b">⚠️ Conflict</span>
                <span class="event-timestamp">{{ formatTimestamp(entry.timestamp) }}</span>
              </div>
              <div class="feed-entry-body" style="color:#f59e0b">
                {{ agentShortName(entry.agentA || '') }} vs {{ agentShortName(entry.agentB || '') }}
                on <span class="mono">{{ entry.device }}</span>
                → <strong>{{ agentShortName(entry.winner || '') }}</strong> wins
              </div>
            </template>

            <!-- Cycle -->
            <template v-else-if="entry.type === 'cycle'">
              <div class="feed-entry-header">
                <span style="font-size:12px;font-weight:600;color:var(--info)">⚙️ Cycle {{ entry.cycleLabel }}</span>
                <span class="event-timestamp">{{ formatTimestamp(entry.timestamp) }}</span>
              </div>
              <div class="feed-entry-body">
                {{ entry.decisions }} decisions, {{ entry.errors }} errors
                <span v-if="entry.blockMined != null"> · block #{{ entry.blockMined }} mined</span>
              </div>
            </template>

            <!-- Session -->
            <template v-else-if="entry.type === 'session'">
              <div class="feed-entry-header">
                <span style="font-size:12px;font-weight:600;color:var(--success)">🔗 Session Created</span>
                <span class="event-timestamp">{{ formatTimestamp(entry.timestamp) }}</span>
              </div>
              <div class="feed-entry-body mono">{{ entry.sessionName }}</div>
            </template>

            <!-- Anomaly Detected -->
            <template v-else-if="entry.type === 'anomaly'">
              <div class="feed-entry-header">
                <span class="agent-tag" style="color:#f97316">📊 Anomaly</span>
                <span class="event-timestamp">{{ formatTimestamp(entry.timestamp) }}</span>
              </div>
              <div class="feed-entry-body">
                <span class="mono" style="font-size:11px">{{ entry.device }} ({{ entry.deviceType }})</span>
                <div class="feed-entry-badges">
                  <div class="confidence-bar">
                    <div class="confidence-fill" style="background:#f97316" :style="{ width: ((entry.anomalyScore || 0) * 100) + '%' }"></div>
                  </div>
                  <span style="font-size:10px;color:var(--text-muted)">score {{ ((entry.anomalyScore || 0) * 100).toFixed(0) }}%</span>
                  <span v-for="d in (entry.detectorsTriggered || [])" :key="d"
                    class="badge badge-neutral" style="font-size:8px">{{ d }}</span>
                </div>
              </div>
            </template>

            <!-- Anomaly Trained -->
            <template v-else-if="entry.type === 'anomaly_trained'">
              <div class="feed-entry-header">
                <span style="font-size:12px;font-weight:600;color:#f97316">📊 Anomaly Models Trained</span>
                <span class="event-timestamp">{{ formatTimestamp(entry.timestamp) }}</span>
              </div>
              <div class="feed-entry-body">
                {{ entry.devicesProfiled }} devices profiled · {{ entry.totalSamples }} samples
              </div>
            </template>

            <!-- Arbitration Resolved -->
            <template v-else-if="entry.type === 'arbitration'">
              <div class="feed-entry-header">
                <span style="font-size:12px;font-weight:600;color:#a855f7">⚖️ Arbitration</span>
                <span class="event-timestamp">{{ formatTimestamp(entry.timestamp) }}</span>
              </div>
              <div class="feed-entry-body">
                <span class="mono" style="font-size:11px">{{ entry.device }}</span>
                → <strong>{{ agentShortName(entry.winnerAgent || '') }}</strong> wins
                <span class="badge badge-neutral" style="font-size:8px;margin-left:4px">{{ entry.method }}</span>
                <div v-if="entry.loserAgents?.length" style="font-size:10px;color:var(--text-muted);margin-top:2px">
                  overrides: {{ entry.loserAgents.map(a => agentShortName(a)).join(', ') }}
                </div>
              </div>
            </template>
          </div>
        </div>
      </div>

      <!-- RIGHT: Agent Detail (also used as overlay on narrow screens) -->
      <div class="am-detail-panel" :class="{ 'overlay-open': showDetailOverlay }">
        <div v-if="selectedAgent && agentDetail" class="am-detail-content">
          <div class="panel-header" style="padding:10px 12px">
            <span class="panel-title">
              {{ getRoleConfig(selectedAgent.role).icon }} {{ getRoleConfig(selectedAgent.role).label }}
            </span>
            <div style="display:flex;align-items:center;gap:6px">
              <div class="agent-status-dot" :class="selectedAgentState?.status || 'idle'" style="width:10px;height:10px"></div>
              <button class="btn-close-detail" @click="showDetailOverlay = false">✕</button>
            </div>
          </div>

          <!-- Info -->
          <div class="detail-section">
            <div class="detail-row">
              <span class="detail-label">Agent ID</span>
              <span class="mono" style="font-size:10px">{{ agentDetail.agent_id }}</span>
            </div>
            <div class="detail-row">
              <span class="detail-label">Role</span>
              <span>{{ agentDetail.role }}</span>
            </div>
            <div class="detail-row">
              <span class="detail-label">Priority</span>
              <span>{{ agentDetail.priority }}</span>
            </div>
            <div class="detail-row">
              <span class="detail-label">Model</span>
              <span class="mono" style="font-size:10px">{{ agentDetail.model }}</span>
            </div>
            <div v-if="agentDetail.description" class="detail-desc">{{ agentDetail.description }}</div>
          </div>

          <!-- Decision Stats -->
          <div class="detail-section">
            <div class="detail-section-title">Decision Stats</div>
            <div class="detail-stats-grid">
              <div class="detail-stat">
                <span class="detail-stat-value">{{ selectedAgentState?.decisionCount || agentDetail.decision_stats?.total || 0 }}</span>
                <span class="detail-stat-label">Decisions</span>
              </div>
              <div class="detail-stat">
                <span class="detail-stat-value" style="color:#22c55e">{{ formatPercent(agentDetail.decision_stats?.acceptance_rate || 0) }}</span>
                <span class="detail-stat-label">Acceptance</span>
              </div>
              <div class="detail-stat">
                <span class="detail-stat-value" style="color:#f59e0b">{{ formatPercent(agentDetail.decision_stats?.conflict_rate || 0) }}</span>
                <span class="detail-stat-label">Conflict</span>
              </div>
            </div>
            <div class="acceptance-bar-track">
              <div class="acceptance-bar-fill" :style="{ width: ((agentDetail.decision_stats?.acceptance_rate || 0) * 100) + '%' }"></div>
            </div>
          </div>

          <!-- Device Types -->
          <div v-if="agentDetail.allowed_device_types?.length" class="detail-section">
            <div class="detail-section-title">Allowed Device Types</div>
            <div style="display:flex;flex-wrap:wrap;gap:4px">
              <span v-for="dt in agentDetail.allowed_device_types" :key="dt"
                class="badge badge-neutral mono" style="font-size:9px">{{ dt }}</span>
            </div>
          </div>

          <!-- Permissions -->
          <div v-if="agentDetail.permissions?.length" class="detail-section">
            <div class="detail-section-title">Permissions ({{ agentDetail.permissions.length }})</div>
            <div class="permissions-list">
              <div v-for="p in agentDetail.permissions" :key="p.device_id + p.command" class="permission-item">
                <span class="mono" style="font-size:10px">{{ p.device_id }}</span>
                <span class="badge badge-neutral" style="font-size:9px">{{ p.command }}</span>
              </div>
            </div>
          </div>

          <!-- Recent Decisions -->
          <div v-if="agentDecisions.length" class="detail-section" style="border-bottom:none">
            <div class="detail-section-title">Recent Decisions ({{ agentDecisions.length }})</div>
            <div class="decisions-scroll">
              <div v-for="(d, i) in agentDecisions" :key="i" class="decision-item">
                <div class="decision-item-header">
                  <span class="mono" style="font-size:11px">{{ d.action }}</span>
                  <span class="badge" :class="d.accepted ? 'badge-success' : 'badge-danger'" style="font-size:9px">
                    {{ d.accepted ? '✓' : '✗' }}
                  </span>
                </div>
                <div style="font-size:10px;color:var(--text-muted)">
                  → {{ d.target_device }} · conf: {{ (d.confidence * 100).toFixed(0) }}%
                  <span v-if="d.conflict"> · conflict ({{ d.conflict_winner }})</span>
                </div>
                <div v-if="d.reasoning_summary" style="font-size:10px;color:var(--text-secondary);margin-top:2px">
                  {{ d.reasoning_summary }}
                </div>
              </div>
            </div>
          </div>

          <div v-if="detailLoading" style="text-align:center;padding:12px">
            <div class="spinner"></div>
          </div>
        </div>

        <!-- Empty state -->
        <div v-else class="am-detail-empty">
          <div class="empty-state">
            <div style="font-size:36px">🤖</div>
            <div style="font-size:13px;font-weight:500">Select an Agent</div>
            <div style="font-size:11px">Click an agent to view details</div>
          </div>
        </div>
      </div>
    </div>
  </div>
</template>

<style scoped>
.agent-monitor {
  display: flex;
  flex-direction: column;
  height: calc(100vh - 100px);
  gap: 10px;
}

/* ── Header ── */
.am-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
  padding: 10px 16px;
  background: var(--bg-secondary);
  border-radius: 8px;
  flex-shrink: 0;
}
.header-left, .header-right { display: flex; align-items: center; gap: 8px; flex-wrap: wrap; }
.header-sep { color: var(--border); font-size: 14px; }

/* Run Configuration */
.run-config { display: flex; align-items: center; gap: 8px; }
.config-field { display: flex; flex-direction: column; gap: 1px; }
.config-label {
  font-size: 9px; color: var(--text-muted); text-transform: uppercase;
  letter-spacing: 0.5px; font-weight: 600;
}
.config-input-group { display: flex; align-items: center; gap: 3px; }
.config-input {
  background: var(--bg-input); color: var(--text-primary);
  border: 1px solid var(--border); border-radius: 4px;
  padding: 3px 6px; font-size: 11px; width: 48px; text-align: center;
  -moz-appearance: textfield;
}
.config-input::-webkit-inner-spin-button,
.config-input::-webkit-outer-spin-button { -webkit-appearance: none; margin: 0; }
.config-input:focus { outline: none; border-color: var(--accent); }
.config-unit { font-size: 9px; color: var(--text-muted); min-width: 20px; }

/* Auto-Run Controls */
.auto-run-controls { display: flex; align-items: center; gap: 8px; }
.auto-run-indicator {
  display: flex; align-items: center; gap: 5px;
  font-size: 11px; font-weight: 600; color: #22c55e;
}
.auto-run-dot {
  width: 8px; height: 8px; border-radius: 50%; background: #22c55e;
  animation: autoRunPulse 1.5s infinite;
}
.remaining-label {
  font-size: 10px; color: var(--text-muted); font-weight: 500;
}
@keyframes autoRunPulse {
  0%, 100% { opacity: 1; box-shadow: 0 0 0 0 rgba(34, 197, 94, 0.5); }
  50% { opacity: 0.7; box-shadow: 0 0 0 4px rgba(34, 197, 94, 0); }
}

/* Button variants */
.btn-outline {
  background: transparent; color: var(--accent);
  border: 1px solid var(--accent); border-radius: 4px;
  padding: 4px 10px; font-size: 12px; cursor: pointer; transition: all 0.15s;
}
.btn-outline:hover { background: var(--accent); color: white; }
.btn-outline:disabled { opacity: 0.4; cursor: not-allowed; }
.btn-success {
  background: #22c55e; color: white; border: none;
  border-radius: 4px; padding: 4px 10px; font-size: 12px; cursor: pointer; transition: all 0.15s;
}
.btn-success:hover { background: #16a34a; }
.btn-success:disabled { opacity: 0.4; cursor: not-allowed; }
.btn-danger {
  background: #ef4444; color: white; border: none;
  border-radius: 4px; padding: 4px 10px; font-size: 12px; cursor: pointer; transition: all 0.15s;
}
.btn-danger:hover { background: #dc2626; }

/* ── Error ── */
.error-bar {
  display: flex; align-items: center; justify-content: space-between;
  padding: 8px 16px; border: 1px solid var(--danger); border-radius: 6px; background: var(--bg-secondary);
}

/* ── Stats Row ── */
.stats-row {
  display: flex; align-items: center; gap: 8px; padding: 0 4px; flex-shrink: 0; flex-wrap: wrap;
}
.mini-stat {
  display: flex; flex-direction: column; align-items: center;
  padding: 4px 10px; background: var(--bg-secondary); border-radius: 6px;
  border: 1px solid var(--border); min-width: 52px;
}
.mini-stat-value { font-size: 15px; font-weight: 700; color: var(--text-primary); }
.mini-stat-label { font-size: 9px; color: var(--text-muted); text-transform: uppercase; letter-spacing: 0.3px; }

/* ── 3-Column Grid ── */
.am-content {
  display: grid;
  grid-template-columns: 280px 1fr 300px;
  gap: 10px;
  flex: 1;
  overflow: hidden;
  padding-bottom: 8px;
}

/* ── Left: Agent Cards ── */
.am-agents-panel {
  display: flex; flex-direction: column;
  background: var(--bg-secondary); border-radius: 8px;
  border: 1px solid var(--border); overflow: hidden;
}
.panel-header {
  display: flex; justify-content: space-between; align-items: center;
  padding: 8px 12px; border-bottom: 1px solid var(--border); flex-shrink: 0;
}
.panel-title { font-size: 13px; font-weight: 600; color: var(--text-primary); }
.panel-subtitle { font-size: 11px; color: var(--text-muted); }
.am-agent-list { flex: 1; overflow-y: auto; padding: 8px; }

.agent-section-header {
  font-size: 10px; font-weight: 600; text-transform: uppercase;
  letter-spacing: 0.06em; color: var(--text-muted); padding: 8px 4px 4px;
}

.am-agent-card {
  padding: 8px 10px; margin-bottom: 4px; border-radius: 6px;
  cursor: pointer; border: 1px solid transparent; transition: all 0.15s;
}
.am-agent-card:hover { background: rgba(255,255,255,0.04); border-color: var(--border); }
.am-agent-card.selected { background: var(--accent-glow); border-color: var(--accent); }

.agent-card-top { display: flex; justify-content: space-between; align-items: center; }
.agent-card-left { display: flex; align-items: center; gap: 8px; }
.agent-icon { font-size: 18px; }
.agent-card-name { font-size: 13px; font-weight: 600; color: var(--text-primary); }
.agent-card-meta { font-size: 10px; color: var(--text-muted); }
.agent-card-right { display: flex; align-items: center; gap: 6px; }

.agent-card-stats {
  display: flex; gap: 12px; padding-top: 4px; margin-top: 4px;
  border-top: 1px solid var(--border); font-size: 10px; color: var(--text-muted);
}

/* Status Dots */
.agent-status-dot {
  width: 8px; height: 8px; border-radius: 50%; transition: background-color 0.3s;
}
.agent-status-dot.idle { background: var(--text-muted); }
.agent-status-dot.reasoning { background: #f59e0b; animation: statusPulse 1.5s infinite; }
.agent-status-dot.decided { background: #22c55e; animation: statusFlash 0.6s ease-out; }

@keyframes statusPulse {
  0%, 100% { opacity: 1; box-shadow: 0 0 0 0 rgba(245, 158, 11, 0.4); }
  50% { opacity: 0.6; box-shadow: 0 0 0 4px rgba(245, 158, 11, 0); }
}
@keyframes statusFlash {
  0% { box-shadow: 0 0 0 4px rgba(34, 197, 94, 0.4); }
  100% { box-shadow: 0 0 0 0 rgba(34, 197, 94, 0); }
}

/* ── Center: Activity Feed ── */
.am-feed-panel {
  display: flex; flex-direction: column;
  background: var(--bg-secondary); border-radius: 8px;
  border: 1px solid var(--border); overflow: hidden;
}
.feed-header {
  display: flex; justify-content: space-between; align-items: center;
  padding: 8px 12px; border-bottom: 1px solid var(--border); flex-shrink: 0;
}
.am-feed-content { flex: 1; overflow-y: auto; padding: 4px; }

.filter-btn {
  padding: 2px 8px; font-size: 11px;
  border: 1px solid var(--border); background: transparent; color: var(--text-muted);
  border-radius: 4px; cursor: pointer; transition: all 0.15s;
}
.filter-btn:hover { background: var(--bg-hover); color: var(--text-primary); }
.filter-btn.active { background: var(--accent); border-color: var(--accent); color: white; }

.feed-entry {
  padding: 6px 8px; margin-bottom: 3px;
  border-left: 3px solid var(--border); border-radius: 0 4px 4px 0;
  background: var(--bg-input); transition: background 0.15s;
}
.feed-entry:hover { background: var(--bg-hover); }
.feed-decision { border-left-color: var(--accent); }
.feed-conflict { border-left-color: #f59e0b; background: rgba(245,158,11,0.05); }
.feed-cycle { border-left-color: var(--info); background: rgba(96,165,250,0.05); }
.feed-session { border-left-color: var(--success); background: rgba(52,211,153,0.05); }
.feed-anomaly { border-left-color: #f97316; background: rgba(249,115,22,0.05); }
.feed-anomaly_trained { border-left-color: #f97316; background: rgba(249,115,22,0.08); }
.feed-arbitration { border-left-color: #a855f7; background: rgba(168,85,247,0.05); }

.feed-entry-header {
  display: flex; align-items: center; justify-content: space-between; margin-bottom: 2px;
}
.agent-tag { font-size: 12px; font-weight: 600; }
.event-timestamp { font-size: 10px; color: var(--text-muted); font-family: monospace; }
.feed-entry-body { font-size: 11px; color: var(--text-secondary); }
.feed-entry-badges { display: flex; align-items: center; gap: 6px; margin-top: 3px; }

.confidence-bar {
  width: 48px; height: 4px; background: var(--bg-secondary); border-radius: 2px; overflow: hidden;
}
.confidence-fill {
  height: 100%; background: var(--accent); border-radius: 2px; transition: width 0.3s;
}

/* ── Right: Detail Panel ── */
.am-detail-panel {
  display: flex; flex-direction: column;
  background: var(--bg-secondary); border-radius: 8px;
  border: 1px solid var(--border); overflow: hidden;
}
.am-detail-content { flex: 1; overflow-y: auto; padding: 0; }
.am-detail-empty { display: flex; align-items: center; justify-content: center; height: 100%; }

.detail-section { padding: 10px 12px; border-bottom: 1px solid var(--border); }
.detail-section-title {
  font-size: 10px; font-weight: 600; text-transform: uppercase;
  letter-spacing: 0.05em; color: var(--text-muted); margin-bottom: 8px;
}
.detail-row { display: flex; justify-content: space-between; padding: 2px 0; font-size: 12px; }
.detail-label { color: var(--text-muted); }
.detail-desc {
  font-size: 11px; color: var(--text-secondary); margin-top: 6px;
  line-height: 1.4; padding: 6px 8px; background: var(--bg-input); border-radius: 4px;
}

.detail-stats-grid { display: grid; grid-template-columns: 1fr 1fr 1fr; gap: 6px; }
.detail-stat { text-align: center; }
.detail-stat-value { font-size: 18px; font-weight: 700; color: var(--text-primary); display: block; }
.detail-stat-label { font-size: 9px; color: var(--text-muted); text-transform: uppercase; }

.acceptance-bar-track {
  height: 6px; background: var(--bg-input); border-radius: 3px; margin-top: 8px; overflow: hidden;
}
.acceptance-bar-fill {
  height: 100%; background: linear-gradient(90deg, #ef4444, #f59e0b, #22c55e);
  border-radius: 3px; transition: width 0.5s;
}

.permissions-list { max-height: 120px; overflow-y: auto; }
.permission-item {
  display: flex; justify-content: space-between; align-items: center;
  padding: 3px 0; font-size: 11px; border-bottom: 1px solid var(--border);
}

.decisions-scroll { max-height: 250px; overflow-y: auto; }
.decision-item { padding: 6px 0; border-bottom: 1px solid var(--border); }
.decision-item-header { display: flex; justify-content: space-between; align-items: center; }

/* ── Utility ── */
.empty-state {
  display: flex; align-items: center; justify-content: center;
  flex-direction: column; gap: 6px; color: var(--text-muted);
  height: 100%; text-align: center;
}

@keyframes pulse {
  0%, 100% { opacity: 1; } 50% { opacity: 0.6; }
}

/* ── Close button (only visible in overlay mode) ── */
.btn-close-detail {
  display: none; background: none; border: none; color: var(--text-muted);
  font-size: 16px; cursor: pointer; padding: 2px 6px; border-radius: 4px;
}
.btn-close-detail:hover { background: var(--bg-hover); color: var(--text-primary); }

/* ── Responsive: detail panel becomes overlay ── */
@media (max-width: 1100px) {
  .am-content { grid-template-columns: 240px 1fr; }
  .am-detail-panel {
    display: none; position: fixed; top: 100px; right: 0; bottom: 0;
    width: 320px; z-index: 50; border-radius: 8px 0 0 0;
    box-shadow: -4px 0 20px rgba(0,0,0,0.4);
  }
  .am-detail-panel.overlay-open { display: flex; }
  .btn-close-detail { display: block; }
}
</style>
