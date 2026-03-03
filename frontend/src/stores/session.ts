import { defineStore } from 'pinia'
import { ref } from 'vue'
import { getActiveSession, getHealthS5HES, getDeviceMode } from '@/services/apiClient'

export const useSessionStore = defineStore('session', () => {
  const active = ref(false)
  const sessionName = ref('')
  const isFresh = ref(true)
  const devices = ref(0)
  const agents = ref(0)
  const blockchainBlocks = ref(0)
  const subsystemsReady = ref(0)
  const subsystemsTotal = ref(0)
  const s5HesAvailable = ref(false)

  // Device mode
  const deviceMode = ref<string | null>(null)
  const realDevices = ref(0)
  const simulatedDevices = ref(0)

  async function refresh() {
    try {
      const resp = await getActiveSession()
      const d = resp.data
      active.value = d.active
      sessionName.value = d.session_name || ''
      isFresh.value = d.is_fresh
      devices.value = d.devices || 0
      agents.value = d.agents || 0
      blockchainBlocks.value = d.blockchain_blocks || 0
      subsystemsReady.value = d.subsystems_ready || 0
      subsystemsTotal.value = d.subsystems_total || 0
    } catch { /* ignore */ }

    // Fetch device mode if session is active
    if (active.value) {
      try {
        const modeResp = await getDeviceMode()
        const m = modeResp.data
        deviceMode.value = m.mode || 'simulation'
        realDevices.value = m.real_devices || 0
        simulatedDevices.value = m.simulated_devices || 0
      } catch {
        deviceMode.value = 'simulation'
        realDevices.value = 0
        simulatedDevices.value = 0
      }
    } else {
      deviceMode.value = null
      realDevices.value = 0
      simulatedDevices.value = 0
    }
  }

  async function checkS5HES() {
    try {
      const resp = await getHealthS5HES()
      s5HesAvailable.value = resp.data.available || false
    } catch {
      s5HesAvailable.value = false
    }
  }

  return {
    active, sessionName, isFresh, devices, agents,
    blockchainBlocks, subsystemsReady, subsystemsTotal,
    s5HesAvailable, deviceMode, realDevices, simulatedDevices,
    refresh, checkS5HES,
  }
})
