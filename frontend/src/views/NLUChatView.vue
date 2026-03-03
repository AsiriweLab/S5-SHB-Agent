<script setup lang="ts">
import { ref, onMounted, nextTick } from 'vue'
import * as api from '@/services/apiClient'

interface Message {
  role: 'user' | 'system'
  text: string
  timestamp?: string
  intent?: string
  confidence?: number
}

const messages = ref<Message[]>([])
const input = ref('')
const loading = ref(false)
const stats = ref<any>(null)
const chatArea = ref<HTMLElement | null>(null)

async function loadHistory() {
  try {
    const resp = await api.getNLUHistory()
    const hist = Array.isArray(resp.data) ? resp.data : (resp.data.history || [])
    messages.value = hist.map((h: any) => ([
      { role: 'user' as const, text: h.user_text || h.command || h.text || h.input, timestamp: h.timestamp },
      {
        role: 'system' as const,
        text: h.response || h.result || 'No response recorded.',
        intent: h.intent_type || h.intent,
        confidence: h.confidence,
        timestamp: h.timestamp,
      },
    ])).flat()
  } catch { /* ignore */ }
}

async function loadStats() {
  try {
    const resp = await api.getNLUStats()
    stats.value = resp.data
  } catch { /* ignore */ }
}

async function send() {
  const text = input.value.trim()
  if (!text || loading.value) return
  input.value = ''

  messages.value.push({ role: 'user', text, timestamp: new Date().toISOString() })
  scrollBottom()

  loading.value = true
  try {
    const resp = await api.sendNLUCommand({ text })
    const d = resp.data
    messages.value.push({
      role: 'system',
      text: d.response || d.query_response || d.result || 'No response generated.',
      intent: d.intent || d.intent_type,
      confidence: d.confidence,
      timestamp: new Date().toISOString(),
    })
    loadStats()
  } catch (e: any) {
    messages.value.push({
      role: 'system',
      text: `Error: ${e.response?.data?.detail || e.message || 'Command failed'}`,
    })
  }
  loading.value = false
  scrollBottom()
}

function scrollBottom() {
  nextTick(() => {
    if (chatArea.value) chatArea.value.scrollTop = chatArea.value.scrollHeight
  })
}

onMounted(() => {
  loadHistory()
  loadStats()
})
</script>

<template>
  <div style="display:grid;grid-template-columns:1fr 260px;gap:16px;height:calc(100vh - var(--header-h) - var(--footer-h) - 40px)">
    <!-- Chat Panel -->
    <div style="display:flex;flex-direction:column;min-height:0">
      <h1 class="page-title">NLU Chat</h1>

      <div ref="chatArea" class="card" style="flex:1;overflow-y:auto;padding:16px;margin-bottom:0;display:flex;flex-direction:column">
        <div class="chat-messages" style="flex:1">
          <div v-if="!messages.length" class="empty-state" style="flex:1">
            <div class="icon">💬</div>
            <div>Send a natural language command to control your smart home.</div>
            <div class="text-muted mt-8" style="font-size:12px">
              Try: "Turn on the living room lights" or "Set temperature to 72"
            </div>
          </div>
          <div v-for="(m, i) in messages" :key="i" class="chat-bubble" :class="m.role">
            <div>{{ m.text }}</div>
            <div v-if="m.intent" style="font-size:10px;margin-top:4px;opacity:0.7">
              Intent: {{ m.intent }}
              <span v-if="m.confidence != null"> ({{ (m.confidence * 100).toFixed(0) }}%)</span>
            </div>
          </div>
          <div v-if="loading" class="chat-bubble system">
            <div class="spinner" style="width:16px;height:16px"></div>
          </div>
        </div>
      </div>

      <div class="chat-input-bar">
        <input class="form-input" v-model="input" placeholder="Type a command..."
          @keyup.enter="send" :disabled="loading" />
        <button class="btn btn-primary" @click="send" :disabled="!input.trim() || loading">Send</button>
      </div>
    </div>

    <!-- Stats Sidebar -->
    <div>
      <div class="card">
        <div class="card-header"><h3>NLU Stats</h3></div>
        <div v-if="stats">
          <div class="stat-card" style="margin-bottom:8px">
            <div class="label">Total Commands</div>
            <div class="value">{{ stats.total_commands ?? stats.total ?? 0 }}</div>
          </div>
          <div class="stat-card" style="margin-bottom:8px">
            <div class="label">Success Rate</div>
            <div class="value">{{ stats.success_rate != null ? (stats.success_rate * 100).toFixed(0) + '%' : '—' }}</div>
          </div>
          <div class="stat-card" style="margin-bottom:8px">
            <div class="label">Avg Confidence</div>
            <div class="value">{{ stats.avg_confidence != null ? (stats.avg_confidence * 100).toFixed(0) + '%' : '—' }}</div>
          </div>
          <div v-if="stats.intent_distribution" style="margin-top:12px">
            <strong style="font-size:12px">Intent Distribution</strong>
            <div v-for="(count, intent) in stats.intent_distribution" :key="intent as string"
              style="display:flex;justify-content:space-between;padding:4px 0;font-size:12px;border-bottom:1px solid var(--border)">
              <span class="mono">{{ intent }}</span>
              <span class="text-muted">{{ count }}</span>
            </div>
          </div>
        </div>
        <div v-else class="text-muted" style="font-size:12px">No stats available.</div>
      </div>
    </div>
  </div>
</template>
