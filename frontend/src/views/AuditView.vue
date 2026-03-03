<script setup lang="ts">
import { ref, onMounted } from 'vue'
import * as api from '@/services/apiClient'

const loading = ref(false)
const error = ref('')
const auditResults = ref<any>(null)
const report = ref<any>(null)
const scenarios = ref<any[]>([])
const categories = ref<any[]>([])
const tab = ref<'audit' | 'report' | 'scenarios'>('audit')

async function runAudit() {
  loading.value = true
  error.value = ''
  try {
    await api.runAudit()
    await loadAuditResults()
  } catch (e: any) {
    error.value = e.response?.data?.detail || 'Audit failed'
  }
  loading.value = false
}

async function loadAuditResults() {
  try {
    const resp = await api.getAuditResults()
    auditResults.value = resp.data
  } catch { /* ignore */ }
}

async function loadReport() {
  try {
    const resp = await api.getReport()
    report.value = resp.data
  } catch { /* ignore */ }
}

async function loadScenarios() {
  try {
    const [sResp, cResp] = await Promise.all([api.getScenarios(), api.getScenarioCategories()])
    scenarios.value = Array.isArray(sResp.data) ? sResp.data : (sResp.data.scenarios || [])
    categories.value = Array.isArray(cResp.data) ? cResp.data : (cResp.data.categories || [])
  } catch { /* ignore */ }
}

async function runAllScenarios() {
  loading.value = true
  error.value = ''
  try {
    await api.runAllScenarios()
    await loadScenarios()
  } catch (e: any) {
    error.value = e.response?.data?.detail || 'Failed to run scenarios'
  }
  loading.value = false
}

async function runScenario(id: number) {
  loading.value = true
  try {
    await api.runScenario(id)
    await loadScenarios()
  } catch (e: any) {
    error.value = e.response?.data?.detail || 'Failed'
  }
  loading.value = false
}

function switchTab(t: typeof tab.value) {
  tab.value = t
  if (t === 'audit') loadAuditResults()
  else if (t === 'report') loadReport()
  else if (t === 'scenarios') loadScenarios()
}

function statusClass(status: string) {
  if (status === 'pass' || status === 'passed' || status === 'success') return 'badge-success'
  if (status === 'fail' || status === 'failed' || status === 'error') return 'badge-danger'
  if (status === 'warning') return 'badge-warning'
  return 'badge-neutral'
}

onMounted(() => {
  loadAuditResults()
})
</script>

<template>
  <div>
    <div class="flex-between mb-16">
      <h1 class="page-title" style="margin-bottom:0">Audit & Scenarios</h1>
      <div class="flex gap-8">
        <button class="btn btn-primary btn-sm" @click="runAudit" :disabled="loading">Run Audit</button>
        <button class="btn btn-secondary btn-sm" @click="runAllScenarios" :disabled="loading">Run All Scenarios</button>
      </div>
    </div>

    <div v-if="error" class="card" style="border-color:var(--danger);margin-bottom:12px">
      <span class="text-danger">{{ error }}</span>
      <button class="btn btn-sm btn-secondary" style="margin-left:12px" @click="error=''">Dismiss</button>
    </div>

    <div class="tab-bar">
      <div class="tab" :class="{ active: tab === 'audit' }" @click="switchTab('audit')">Audit</div>
      <div class="tab" :class="{ active: tab === 'report' }" @click="switchTab('report')">Report</div>
      <div class="tab" :class="{ active: tab === 'scenarios' }" @click="switchTab('scenarios')">Scenarios</div>
    </div>

    <!-- Audit Tab -->
    <div v-if="tab === 'audit'">
      <div v-if="auditResults" class="card">
        <div class="card-header">
          <h3>Audit Results</h3>
          <span class="badge" :class="statusClass(auditResults.overall_status || auditResults.status || '')">
            {{ auditResults.overall_status || auditResults.status || 'Unknown' }}
          </span>
        </div>

        <!-- Summary -->
        <div v-if="auditResults.summary" class="stat-grid" style="margin-bottom:16px">
          <div class="stat-card">
            <div class="label">Total Checks</div>
            <div class="value">{{ auditResults.summary.total ?? 0 }}</div>
          </div>
          <div class="stat-card">
            <div class="label">Passed</div>
            <div class="value text-success">{{ auditResults.summary.passed ?? 0 }}</div>
          </div>
          <div class="stat-card">
            <div class="label">Failed</div>
            <div class="value text-danger">{{ auditResults.summary.failed ?? 0 }}</div>
          </div>
          <div class="stat-card">
            <div class="label">Warnings</div>
            <div class="value text-warning">{{ auditResults.summary.warnings ?? 0 }}</div>
          </div>
        </div>

        <!-- Checks -->
        <div v-if="auditResults.checks?.length">
          <table class="data-table">
            <thead><tr><th>Check</th><th>Category</th><th>Status</th><th>Details</th></tr></thead>
            <tbody>
              <tr v-for="(c, i) in auditResults.checks" :key="i">
                <td>{{ c.name || c.check }}</td>
                <td class="mono" style="font-size:12px">{{ c.category || '—' }}</td>
                <td><span class="badge" :class="statusClass(c.status)">{{ c.status }}</span></td>
                <td class="text-muted" style="font-size:12px">{{ c.message || c.details || '—' }}</td>
              </tr>
            </tbody>
          </table>
        </div>
      </div>
      <div v-else class="card">
        <div class="empty-state">
          <div class="icon">📋</div>
          <div>Click "Run Audit" to perform a comprehensive system audit.</div>
        </div>
      </div>
    </div>

    <!-- Report Tab -->
    <div v-if="tab === 'report'">
      <div v-if="report" class="card">
        <div class="card-header"><h3>System Report</h3></div>
        <div v-if="typeof report === 'object'">
          <div v-for="(val, key) in report" :key="key as string" style="margin-bottom:16px">
            <strong style="font-size:13px;text-transform:capitalize">{{ (key as string).replace(/_/g, ' ') }}</strong>
            <pre v-if="typeof val === 'object'"
              style="background:var(--bg-input);padding:12px;border-radius:4px;overflow-x:auto;font-size:11px;margin-top:6px">{{ JSON.stringify(val, null, 2) }}</pre>
            <div v-else class="text-secondary" style="font-size:13px;margin-top:4px">{{ val }}</div>
          </div>
        </div>
        <pre v-else style="background:var(--bg-input);padding:12px;border-radius:4px;font-size:11px">{{ JSON.stringify(report, null, 2) }}</pre>
      </div>
      <div v-else class="card">
        <div class="empty-state">No report data. Run an audit or create a session first.</div>
      </div>
    </div>

    <!-- Scenarios Tab -->
    <div v-if="tab === 'scenarios'">
      <!-- Categories -->
      <div v-if="categories.length" class="card" style="padding:12px 16px">
        <div class="flex" style="flex-wrap:wrap;gap:8px">
          <span v-for="c in categories" :key="c.id || c.name || c" class="badge badge-neutral">
            {{ c.name || c }}
          </span>
        </div>
      </div>

      <div class="card">
        <div class="card-header">
          <h3>Scenarios ({{ scenarios.length }})</h3>
          <button class="btn btn-secondary btn-sm" @click="loadScenarios" :disabled="loading">Refresh</button>
        </div>
        <div v-if="loading" class="empty-state"><div class="spinner"></div></div>
        <table v-else-if="scenarios.length" class="data-table">
          <thead><tr><th>ID</th><th>Name</th><th>Category</th><th>Status</th><th>Result</th><th></th></tr></thead>
          <tbody>
            <tr v-for="s in scenarios" :key="s.id">
              <td>{{ s.id }}</td>
              <td>{{ s.name }}</td>
              <td class="mono" style="font-size:12px">{{ s.category || '—' }}</td>
              <td><span class="badge" :class="statusClass(s.status || '')">{{ s.status || 'pending' }}</span></td>
              <td class="text-muted" style="font-size:12px">{{ s.result || s.message || '—' }}</td>
              <td><button class="btn btn-sm btn-primary" @click="runScenario(s.id)" :disabled="loading">Run</button></td>
            </tr>
          </tbody>
        </table>
        <div v-else class="empty-state">No scenarios available. Create a session first.</div>
      </div>
    </div>
  </div>
</template>
