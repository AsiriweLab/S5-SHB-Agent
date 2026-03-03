<script setup lang="ts">
import { ref, onMounted } from 'vue'
import * as api from '@/services/apiClient'

const tab = ref<'detect' | 'models' | 'stats'>('detect')
const loading = ref(false)
const error = ref('')
const success = ref('')

const anomalyStats = ref<any>(null)
const models = ref<any[]>([])
const detectionResults = ref<any>(null)

async function loadStats() {
  try {
    const resp = await api.getAnomalyStats()
    anomalyStats.value = resp.data
  } catch { /* ignore */ }
}

async function loadModels() {
  try {
    const resp = await api.getAnomalyModels()
    models.value = Array.isArray(resp.data) ? resp.data : (resp.data.models || [])
  } catch { /* ignore */ }
}

async function train() {
  loading.value = true
  error.value = ''
  success.value = ''
  try {
    const resp = await api.trainAnomaly()
    success.value = resp.data.message || 'Training complete'
    await Promise.all([loadModels(), loadStats()])
  } catch (e: any) {
    error.value = e.response?.data?.detail || 'Training failed'
  }
  loading.value = false
}

async function detect() {
  loading.value = true
  error.value = ''
  try {
    const resp = await api.detectAnomaly()
    detectionResults.value = resp.data
    await loadStats()
  } catch (e: any) {
    error.value = e.response?.data?.detail || 'Detection failed'
  }
  loading.value = false
}

async function accumulate() {
  loading.value = true
  error.value = ''
  success.value = ''
  try {
    const resp = await api.accumulateTelemetry()
    success.value = resp.data.message || 'Telemetry accumulated'
  } catch (e: any) {
    error.value = e.response?.data?.detail || 'Accumulation failed'
  }
  loading.value = false
}

onMounted(() => {
  loadStats()
  loadModels()
})
</script>

<template>
  <div>
    <div class="flex-between mb-16">
      <h1 class="page-title" style="margin-bottom:0">Anomaly Detection</h1>
      <div class="flex gap-8">
        <button class="btn btn-secondary btn-sm" @click="accumulate" :disabled="loading">Accumulate Data</button>
        <button class="btn btn-primary btn-sm" @click="train" :disabled="loading">Train Models</button>
        <button class="btn btn-success btn-sm" @click="detect" :disabled="loading">Run Detection</button>
      </div>
    </div>

    <div v-if="error" class="card" style="border-color:var(--danger);margin-bottom:12px">
      <span class="text-danger">{{ error }}</span>
      <button class="btn btn-sm btn-secondary" style="margin-left:12px" @click="error=''">Dismiss</button>
    </div>
    <div v-if="success" class="card" style="border-color:var(--success);margin-bottom:12px">
      <span class="text-success">{{ success }}</span>
      <button class="btn btn-sm btn-secondary" style="margin-left:12px" @click="success=''">Dismiss</button>
    </div>

    <div class="tab-bar">
      <div class="tab" :class="{ active: tab === 'detect' }" @click="tab = 'detect'">Detection</div>
      <div class="tab" :class="{ active: tab === 'models' }" @click="tab = 'models'">Models</div>
      <div class="tab" :class="{ active: tab === 'stats' }" @click="tab = 'stats'">Statistics</div>
    </div>

    <!-- Detection Tab -->
    <div v-if="tab === 'detect'">
      <div v-if="detectionResults" class="card">
        <div class="card-header"><h3>Detection Results</h3></div>
        <div v-if="detectionResults.anomalies?.length">
          <table class="data-table">
            <thead><tr><th>Device</th><th>Type</th><th>Score</th><th>Severity</th><th>Details</th></tr></thead>
            <tbody>
              <tr v-for="(a, i) in detectionResults.anomalies" :key="i">
                <td class="mono" style="font-size:12px">{{ a.device_id || '—' }}</td>
                <td><span class="badge badge-warning">{{ a.anomaly_type || a.type || '—' }}</span></td>
                <td>{{ a.score?.toFixed(3) ?? '—' }}</td>
                <td>
                  <span class="badge" :class="a.severity === 'high' ? 'badge-danger' : a.severity === 'medium' ? 'badge-warning' : 'badge-info'">
                    {{ a.severity || '—' }}
                  </span>
                </td>
                <td class="text-muted" style="font-size:12px">{{ a.details || a.message || '—' }}</td>
              </tr>
            </tbody>
          </table>
        </div>
        <div v-else class="empty-state">
          <div>No anomalies detected. {{ detectionResults.message || '' }}</div>
        </div>
      </div>
      <div v-else class="card">
        <div class="empty-state">
          <div class="icon">🔍</div>
          <div>Click "Run Detection" to scan for anomalies in current telemetry data.</div>
        </div>
      </div>
    </div>

    <!-- Models Tab -->
    <div v-if="tab === 'models'" class="card">
      <div class="card-header">
        <h3>Trained Models ({{ models.length }})</h3>
        <button class="btn btn-secondary btn-sm" @click="loadModels">Refresh</button>
      </div>
      <table v-if="models.length" class="data-table">
        <thead><tr><th>Model</th><th>Type</th><th>Status</th><th>Accuracy</th><th>Trained At</th></tr></thead>
        <tbody>
          <tr v-for="m in models" :key="m.id || m.name">
            <td>{{ m.name || m.id }}</td>
            <td class="mono" style="font-size:12px">{{ m.model_type || m.type || '—' }}</td>
            <td>
              <span class="badge" :class="m.status === 'ready' || m.trained ? 'badge-success' : 'badge-neutral'">
                {{ m.status || (m.trained ? 'Ready' : 'Untrained') }}
              </span>
            </td>
            <td>{{ m.accuracy != null ? (m.accuracy * 100).toFixed(1) + '%' : '—' }}</td>
            <td class="text-muted" style="font-size:12px">{{ m.trained_at || '—' }}</td>
          </tr>
        </tbody>
      </table>
      <div v-else class="empty-state">No models trained yet. Click "Train Models" to start.</div>
    </div>

    <!-- Stats Tab -->
    <div v-if="tab === 'stats'">
      <div v-if="anomalyStats" class="card">
        <div class="card-header"><h3>Anomaly Statistics</h3></div>
        <div class="stat-grid">
          <div class="stat-card">
            <div class="label">Total Scans</div>
            <div class="value">{{ anomalyStats.total_scans ?? anomalyStats.scans ?? 0 }}</div>
          </div>
          <div class="stat-card">
            <div class="label">Anomalies Found</div>
            <div class="value">{{ anomalyStats.total_anomalies ?? anomalyStats.anomalies_found ?? 0 }}</div>
          </div>
          <div class="stat-card">
            <div class="label">Data Points</div>
            <div class="value">{{ anomalyStats.data_points ?? anomalyStats.samples ?? 0 }}</div>
          </div>
          <div class="stat-card">
            <div class="label">Models</div>
            <div class="value">{{ anomalyStats.models_count ?? models.length }}</div>
          </div>
        </div>
      </div>
      <div v-else class="card">
        <div class="empty-state">No statistics available yet.</div>
      </div>
    </div>
  </div>
</template>
