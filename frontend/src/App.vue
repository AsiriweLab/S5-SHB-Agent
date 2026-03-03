<script setup lang="ts">
import { onMounted, onUnmounted } from 'vue'
import { RouterLink, RouterView } from 'vue-router'
import { useSessionStore } from '@/stores/session'

const session = useSessionStore()
let pollTimer: ReturnType<typeof setInterval>

onMounted(() => {
  session.refresh()
  session.checkS5HES()
  pollTimer = setInterval(() => {
    session.refresh()
    session.checkS5HES()
  }, 10000)
})

onUnmounted(() => clearInterval(pollTimer))
</script>

<template>
  <header class="app-header">
    <div class="logo">
      <span>S5</span> ABC-HS Agent
    </div>
    <div class="session-badge" :class="{ inactive: !session.active }">
      <span class="dot"></span>
      {{ session.active ? session.sessionName || 'Active Session' : 'No Session' }}
    </div>
  </header>

  <nav class="app-sidebar">
    <div class="nav-group">
      <div class="nav-group-label">Overview</div>
      <RouterLink to="/" class="nav-item">Dashboard</RouterLink>
      <RouterLink to="/sessions" class="nav-item">Sessions</RouterLink>
    </div>
    <div class="nav-group">
      <div class="nav-group-label">Configure</div>
      <RouterLink to="/home-builder" class="nav-item">Home Builder</RouterLink>
      <RouterLink to="/threats" class="nav-item">Threat Builder</RouterLink>
      <RouterLink to="/simulation" class="nav-item">Simulation</RouterLink>
    </div>
    <div class="nav-group">
      <div class="nav-group-label">Blockchain</div>
      <RouterLink to="/blockchain" class="nav-item">Explorer</RouterLink>
      <RouterLink to="/agents" class="nav-item">Agent Monitor</RouterLink>
      <RouterLink to="/governance" class="nav-item">Governance</RouterLink>
    </div>
    <div class="nav-group">
      <div class="nav-group-label">Tools</div>
      <RouterLink to="/chat" class="nav-item">NLU Chat</RouterLink>
      <RouterLink to="/anomaly" class="nav-item">Anomaly</RouterLink>
      <RouterLink to="/audit" class="nav-item">Audit</RouterLink>
    </div>
  </nav>

  <main class="app-main">
    <RouterView />
  </main>

  <footer class="app-footer">
    <div>
      <span class="status-dot" :class="session.s5HesAvailable ? 'ok' : 'fail'"></span>
      S5-HES {{ session.s5HesAvailable ? 'Connected' : 'Unavailable' }}
    </div>
    <div>
      Blocks: {{ session.blockchainBlocks }} | Devices: {{ session.devices }} | Agents: {{ session.agents }}
    </div>
  </footer>
</template>
