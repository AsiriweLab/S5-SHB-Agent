<script setup lang="ts">
import { ref, onMounted } from 'vue'
import { useSessionStore } from '@/stores/session'
import { getHealthDetail, getBlockchainSummary, getAgents, getSimulationStatus } from '@/services/apiClient'

const session = useSessionStore()
const health = ref<any>(null)
const blockchain = ref<any>(null)
const agents = ref<any[]>([])
const simulation = ref<any>(null)
const loading = ref(true)

async function refresh() {
  loading.value = true
  try {
    await session.refresh()
    const [hRes, bRes, aRes, sRes] = await Promise.allSettled([
      getHealthDetail(),
      getBlockchainSummary(),
      getAgents(),
      getSimulationStatus(),
    ])
    if (hRes.status === 'fulfilled') health.value = hRes.value.data
    if (bRes.status === 'fulfilled') blockchain.value = bRes.value.data
    if (aRes.status === 'fulfilled') agents.value = aRes.value.data
    if (sRes.status === 'fulfilled') simulation.value = sRes.value.data
  } finally {
    loading.value = false
  }
}

onMounted(refresh)
</script>

<template>
  <div>
    <div class="flex-between mb-16">
      <h1 class="page-title" style="margin-bottom:0">Dashboard</h1>
      <button class="btn btn-secondary btn-sm" @click="refresh" :disabled="loading">Refresh</button>
    </div>

    <!-- Stat Cards -->
    <div class="stat-grid">
      <div class="stat-card">
        <div class="label">Session</div>
        <div class="value" :class="session.active ? 'text-success' : 'text-muted'">
          {{ session.active ? 'Active' : 'None' }}
        </div>
        <div class="text-muted" style="font-size:12px">{{ session.sessionName || '—' }}</div>
      </div>
      <div class="stat-card">
        <div class="label">Device Mode</div>
        <div class="value">
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
          <span v-else class="text-muted" style="font-size:14px">—</span>
        </div>
        <div v-if="session.deviceMode === 'real'" class="text-muted" style="font-size:11px">
          {{ session.realDevices }} real device{{ session.realDevices !== 1 ? 's' : '' }} connected
        </div>
        <div v-else-if="session.deviceMode === 'hybrid'" class="text-muted" style="font-size:11px">
          {{ session.realDevices }} real · {{ session.simulatedDevices }} simulated
        </div>
      </div>
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
      <div class="stat-card">
        <div class="label">S5-HES</div>
        <div class="value" :class="session.s5HesAvailable ? 'text-success' : 'text-danger'">
          {{ session.s5HesAvailable ? 'Online' : 'Offline' }}
        </div>
      </div>
    </div>

    <!-- Health Detail -->
    <div class="card" v-if="health">
      <div class="card-header"><h3>Subsystem Health</h3></div>
      <table class="data-table">
        <thead><tr><th>Subsystem</th><th>Status</th></tr></thead>
        <tbody>
          <tr v-for="(val, key) in health.subsystems" :key="key">
            <td>{{ key }}</td>
            <td>
              <span class="badge" :class="val ? 'badge-success' : 'badge-danger'">
                {{ val ? 'Ready' : 'Down' }}
              </span>
            </td>
          </tr>
        </tbody>
      </table>
    </div>

    <!-- Blockchain Summary -->
    <div class="card" v-if="blockchain">
      <div class="card-header"><h3>Blockchain Summary</h3></div>
      <div class="stat-grid">
        <div class="stat-card">
          <div class="label">Total Blocks</div>
          <div class="value">{{ blockchain.total_blocks ?? 0 }}</div>
        </div>
        <div class="stat-card">
          <div class="label">Transactions</div>
          <div class="value">{{ blockchain.total_transactions ?? 0 }}</div>
        </div>
        <div class="stat-card">
          <div class="label">Registered Agents</div>
          <div class="value">{{ blockchain.registered_agents ?? 0 }}</div>
        </div>
        <div class="stat-card">
          <div class="label">Chain Valid</div>
          <div class="value" :class="blockchain.chain_valid ? 'text-success' : 'text-danger'">
            {{ blockchain.chain_valid ? 'Yes' : 'No' }}
          </div>
        </div>
      </div>
    </div>

    <!-- Agents Quick View -->
    <div class="card" v-if="agents.length">
      <div class="card-header"><h3>Active Agents</h3></div>
      <table class="data-table">
        <thead><tr><th>Agent</th><th>Type</th><th>Status</th></tr></thead>
        <tbody>
          <tr v-for="a in agents" :key="a.id || a.agent_id">
            <td>{{ a.name || a.agent_id }}</td>
            <td class="mono" style="font-size:12px">{{ a.agent_type || a.type || '—' }}</td>
            <td>
              <span class="badge" :class="a.status === 'active' ? 'badge-success' : 'badge-neutral'">
                {{ a.status || 'unknown' }}
              </span>
            </td>
          </tr>
        </tbody>
      </table>
    </div>

    <!-- Simulation Status -->
    <div class="card" v-if="simulation">
      <div class="card-header"><h3>Simulation</h3></div>
      <div class="flex gap-16">
        <div>
          <span class="text-muted">Status:</span>
          <span class="badge" :class="simulation.running ? 'badge-success' : 'badge-neutral'" style="margin-left:8px">
            {{ simulation.running ? 'Running' : 'Stopped' }}
          </span>
        </div>
        <div v-if="simulation.elapsed_hours">
          <span class="text-muted">Elapsed:</span> {{ simulation.elapsed_hours?.toFixed(1) }}h
        </div>
      </div>
    </div>

    <div v-if="!session.active && !loading" class="empty-state" style="margin-top:24px">
      <div class="icon">🏠</div>
      <div>No active session. Go to <RouterLink to="/sessions">Sessions</RouterLink> to create one.</div>
    </div>
  </div>
</template>
