<script setup lang="ts">
import { ref, onMounted, onUnmounted, watch } from 'vue'
import { useWebSocket } from '@/composables/useWebSocket'
import * as api from '@/services/apiClient'

const tab = ref<'blocks' | 'transactions' | 'agents' | 'permissions' | 'conflicts'>('blocks')
const loading = ref(false)

const summary = ref<any>(null)
const blocks = ref<any[]>([])
const transactions = ref<any[]>([])
const agents = ref<any[]>([])
const permissions = ref<any[]>([])
const conflicts = ref<any[]>([])
const adaptivePow = ref<any>(null)
const selectedBlock = ref<any>(null)

// Live updates
const { data: wsData, connected: wsConnected } = useWebSocket('blockchain')
watch(wsData, (val) => {
  if (val) loadSummary()
})

async function loadSummary() {
  try {
    const resp = await api.getBlockchainSummary()
    summary.value = resp.data
  } catch { /* ignore */ }
}

async function loadBlocks() {
  loading.value = true
  try {
    const resp = await api.getBlocks({ limit: 50 })
    blocks.value = Array.isArray(resp.data) ? resp.data : (resp.data.blocks || [])
  } catch { /* ignore */ }
  loading.value = false
}

async function loadTransactions() {
  loading.value = true
  try {
    const resp = await api.getTransactions({ limit: 50 })
    transactions.value = Array.isArray(resp.data) ? resp.data : (resp.data.transactions || [])
  } catch { /* ignore */ }
  loading.value = false
}

async function loadAgents() {
  loading.value = true
  try {
    const resp = await api.getRegisteredAgents()
    agents.value = Array.isArray(resp.data) ? resp.data : (resp.data.agents || [])
  } catch { /* ignore */ }
  loading.value = false
}

async function loadPermissions() {
  loading.value = true
  try {
    const resp = await api.getPermissions()
    permissions.value = Array.isArray(resp.data) ? resp.data : (resp.data.permissions || [])
  } catch { /* ignore */ }
  loading.value = false
}

async function loadConflicts() {
  loading.value = true
  try {
    const [cResp, aResp] = await Promise.all([api.getConflicts(), api.getAdaptivePow()])
    conflicts.value = Array.isArray(cResp.data) ? cResp.data : (cResp.data.conflicts || [])
    adaptivePow.value = aResp.data
  } catch { /* ignore */ }
  loading.value = false
}

async function viewBlock(index: number) {
  try {
    const resp = await api.getBlock(index)
    selectedBlock.value = resp.data
  } catch { /* ignore */ }
}

function switchTab(t: typeof tab.value) {
  tab.value = t
  selectedBlock.value = null
  if (t === 'blocks') loadBlocks()
  else if (t === 'transactions') loadTransactions()
  else if (t === 'agents') loadAgents()
  else if (t === 'permissions') loadPermissions()
  else if (t === 'conflicts') loadConflicts()
}

onMounted(() => {
  loadSummary()
  loadBlocks()
})
</script>

<template>
  <div>
    <div class="flex-between mb-16">
      <h1 class="page-title" style="margin-bottom:0">Blockchain Explorer</h1>
      <span class="badge" :class="wsConnected ? 'badge-success' : 'badge-neutral'">
        WS {{ wsConnected ? 'Live' : 'Off' }}
      </span>
    </div>

    <!-- Summary Cards -->
    <div v-if="summary" class="stat-grid">
      <div class="stat-card">
        <div class="label">Blocks</div>
        <div class="value">{{ summary.total_blocks ?? 0 }}</div>
      </div>
      <div class="stat-card">
        <div class="label">Transactions</div>
        <div class="value">{{ summary.total_transactions ?? 0 }}</div>
      </div>
      <div class="stat-card">
        <div class="label">Agents</div>
        <div class="value">{{ summary.registered_agents ?? 0 }}</div>
      </div>
      <div class="stat-card">
        <div class="label">Chain Valid</div>
        <div class="value" :class="summary.chain_valid ? 'text-success' : 'text-danger'">
          {{ summary.chain_valid ? 'Yes' : 'No' }}
        </div>
      </div>
    </div>

    <!-- Tabs -->
    <div class="tab-bar">
      <div class="tab" :class="{ active: tab === 'blocks' }" @click="switchTab('blocks')">Blocks</div>
      <div class="tab" :class="{ active: tab === 'transactions' }" @click="switchTab('transactions')">Transactions</div>
      <div class="tab" :class="{ active: tab === 'agents' }" @click="switchTab('agents')">Agents</div>
      <div class="tab" :class="{ active: tab === 'permissions' }" @click="switchTab('permissions')">Permissions</div>
      <div class="tab" :class="{ active: tab === 'conflicts' }" @click="switchTab('conflicts')">Conflicts / PoW</div>
    </div>

    <!-- Block Detail Modal -->
    <div v-if="selectedBlock" class="card" style="border-color:var(--accent)">
      <div class="card-header">
        <h3>Block #{{ selectedBlock.index }}</h3>
        <button class="btn btn-sm btn-secondary" @click="selectedBlock = null">Close</button>
      </div>
      <div style="font-size:13px">
        <div class="mb-16"><span class="text-muted">Hash:</span> <span class="mono">{{ selectedBlock.hash }}</span></div>
        <div class="mb-16"><span class="text-muted">Previous:</span> <span class="mono">{{ selectedBlock.previous_hash }}</span></div>
        <div class="mb-16"><span class="text-muted">Timestamp:</span> {{ selectedBlock.timestamp }}</div>
        <div class="mb-16"><span class="text-muted">Nonce:</span> {{ selectedBlock.nonce }}</div>
        <div v-if="selectedBlock.transactions?.length">
          <strong>Transactions ({{ selectedBlock.transactions.length }})</strong>
          <pre style="background:var(--bg-input);padding:12px;border-radius:4px;overflow-x:auto;font-size:11px;margin-top:8px">{{ JSON.stringify(selectedBlock.transactions, null, 2) }}</pre>
        </div>
      </div>
    </div>

    <!-- Blocks Tab -->
    <div v-if="tab === 'blocks' && !selectedBlock" class="card">
      <div v-if="loading" class="empty-state"><div class="spinner"></div></div>
      <table v-else-if="blocks.length" class="data-table">
        <thead><tr><th>#</th><th>Hash</th><th>Transactions</th><th>Timestamp</th><th></th></tr></thead>
        <tbody>
          <tr v-for="b in blocks" :key="b.index">
            <td>{{ b.index }}</td>
            <td class="mono truncate" style="max-width:200px;font-size:11px">{{ b.hash }}</td>
            <td>{{ b.transaction_count ?? b.transactions?.length ?? 0 }}</td>
            <td class="text-muted" style="font-size:12px">{{ b.timestamp }}</td>
            <td><button class="btn btn-sm btn-secondary" @click="viewBlock(b.index)">View</button></td>
          </tr>
        </tbody>
      </table>
      <div v-else class="empty-state">No blocks. Create a session to initialize the blockchain.</div>
    </div>

    <!-- Transactions Tab -->
    <div v-if="tab === 'transactions'" class="card">
      <div v-if="loading" class="empty-state"><div class="spinner"></div></div>
      <table v-else-if="transactions.length" class="data-table">
        <thead><tr><th>Hash</th><th>Type</th><th>Agent</th><th>Timestamp</th></tr></thead>
        <tbody>
          <tr v-for="tx in transactions" :key="tx.hash || tx.tx_hash">
            <td class="mono truncate" style="max-width:180px;font-size:11px">{{ tx.hash || tx.tx_hash }}</td>
            <td><span class="badge badge-info">{{ tx.tx_type || tx.type || '—' }}</span></td>
            <td class="mono" style="font-size:12px">{{ tx.agent_id || '—' }}</td>
            <td class="text-muted" style="font-size:12px">{{ tx.timestamp }}</td>
          </tr>
        </tbody>
      </table>
      <div v-else class="empty-state">No transactions recorded yet.</div>
    </div>

    <!-- Agents Tab -->
    <div v-if="tab === 'agents'" class="card">
      <div v-if="loading" class="empty-state"><div class="spinner"></div></div>
      <table v-else-if="agents.length" class="data-table">
        <thead><tr><th>Agent ID</th><th>Type</th><th>Public Key</th><th>Priority</th></tr></thead>
        <tbody>
          <tr v-for="a in agents" :key="a.agent_id || a.id">
            <td class="mono" style="font-size:12px">{{ a.agent_id || a.id }}</td>
            <td>{{ a.agent_type || a.type || '—' }}</td>
            <td class="mono truncate" style="max-width:160px;font-size:11px">{{ a.public_key || '—' }}</td>
            <td>{{ a.priority ?? '—' }}</td>
          </tr>
        </tbody>
      </table>
      <div v-else class="empty-state">No agents registered on-chain.</div>
    </div>

    <!-- Permissions Tab -->
    <div v-if="tab === 'permissions'" class="card">
      <div v-if="loading" class="empty-state"><div class="spinner"></div></div>
      <table v-else-if="permissions.length" class="data-table">
        <thead><tr><th>Agent</th><th>Device</th><th>Actions</th><th>Granted</th></tr></thead>
        <tbody>
          <tr v-for="(p, i) in permissions" :key="i">
            <td class="mono" style="font-size:12px">{{ p.agent_id }}</td>
            <td class="mono" style="font-size:12px">{{ p.device_id || p.resource || '—' }}</td>
            <td>
              <span v-for="a in (p.actions || [p.action])" :key="a" class="badge badge-info" style="margin-right:4px">{{ a }}</span>
            </td>
            <td class="text-muted" style="font-size:12px">{{ p.timestamp || '—' }}</td>
          </tr>
        </tbody>
      </table>
      <div v-else class="empty-state">No permissions recorded.</div>
    </div>

    <!-- Conflicts Tab -->
    <div v-if="tab === 'conflicts'">
      <div v-if="adaptivePow" class="card">
        <div class="card-header"><h3>Adaptive PoW</h3></div>
        <div class="stat-grid">
          <div class="stat-card">
            <div class="label">Difficulty</div>
            <div class="value">{{ adaptivePow.difficulty ?? adaptivePow.current_difficulty ?? '—' }}</div>
          </div>
          <div class="stat-card">
            <div class="label">Target Time</div>
            <div class="value">{{ adaptivePow.target_time ?? '—' }}s</div>
          </div>
          <div class="stat-card">
            <div class="label">Avg Mining Time</div>
            <div class="value">{{ adaptivePow.avg_mining_time?.toFixed(2) ?? '—' }}s</div>
          </div>
        </div>
      </div>
      <div class="card">
        <div class="card-header"><h3>Conflicts ({{ conflicts.length }})</h3></div>
        <div v-if="loading" class="empty-state"><div class="spinner"></div></div>
        <table v-else-if="conflicts.length" class="data-table">
          <thead><tr><th>ID</th><th>Type</th><th>Agents</th><th>Status</th><th>Resolution</th></tr></thead>
          <tbody>
            <tr v-for="c in conflicts" :key="c.id || c.conflict_id">
              <td class="mono" style="font-size:12px">{{ c.id || c.conflict_id }}</td>
              <td><span class="badge badge-warning">{{ c.conflict_type || c.type || '—' }}</span></td>
              <td class="mono" style="font-size:11px">{{ (c.agents || []).join(', ') || '—' }}</td>
              <td>
                <span class="badge" :class="c.resolved ? 'badge-success' : 'badge-danger'">
                  {{ c.resolved ? 'Resolved' : 'Active' }}
                </span>
              </td>
              <td class="text-muted" style="font-size:12px">{{ c.resolution || '—' }}</td>
            </tr>
          </tbody>
        </table>
        <div v-else class="empty-state">No conflicts detected.</div>
      </div>
    </div>
  </div>
</template>
