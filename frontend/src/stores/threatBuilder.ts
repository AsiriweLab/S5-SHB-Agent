import { defineStore } from 'pinia'
import { ref, watch } from 'vue'
import * as api from '@/services/apiClient'

export interface ThreatEvent {
  id: string
  type: string
  name: string
  startTime: number
  duration: number
  severity: 'low' | 'medium' | 'high' | 'critical'
  severityValue: number
  targetDevice: string
  description: string
  parameters: Record<string, any>
}

export const useThreatBuilderStore = defineStore('threatBuilder', () => {
  // ── Persistent working state ──
  const events = ref<ThreatEvent[]>([])
  const loaded = ref(false)
  const loading = ref(false)
  const threatTypes = ref<any[]>([])

  // ── Auto-sync debounce ──
  let syncTimeout: ReturnType<typeof setTimeout> | null = null
  const syncStatus = ref<'idle' | 'syncing' | 'synced' | 'error'>('idle')

  function scheduleSyncToBackend() {
    if (syncTimeout) clearTimeout(syncTimeout)
    syncTimeout = setTimeout(() => syncToBackend(), 1500)
  }

  // Watch for data changes and auto-sync
  watch(
    () => events.value.length,
    () => {
      if (loaded.value && events.value.length > 0) {
        scheduleSyncToBackend()
      }
    }
  )

  async function syncToBackend() {
    if (events.value.length === 0) return
    syncStatus.value = 'syncing'
    try {
      // Delete existing threats
      const existing = await api.getThreats().catch(() => ({ data: [] }))
      const existingThreats = Array.isArray(existing.data) ? existing.data : (existing.data?.threats || [])
      for (const t of existingThreats) {
        await api.deleteThreat(t.id).catch(() => {})
      }
      // Add current events
      for (const ev of events.value) {
        await api.addThreat({
          name: ev.name,
          threat_type: ev.type,
          severity: ev.severity,
          target_device: ev.targetDevice || undefined,
          parameters: {
            ...ev.parameters,
            start_time: ev.startTime,
            duration: ev.duration,
            severity_value: ev.severityValue,
            description: ev.description,
          },
        }).catch(() => {})
      }
      syncStatus.value = 'synced'
    } catch {
      syncStatus.value = 'error'
    }
  }

  async function loadFromBackend() {
    if (loaded.value && events.value.length > 0) return // Already have data
    loading.value = true
    try {
      const [tResp, typesResp] = await Promise.all([
        api.getThreats(),
        api.getThreatTypes(),
      ])
      const threats = Array.isArray(tResp.data) ? tResp.data : (tResp.data?.threats || [])
      threatTypes.value = Array.isArray(typesResp.data) ? typesResp.data : (typesResp.data?.types || [])

      if (threats.length > 0) {
        events.value = threats.map((t: any, i: number) => ({
          id: t.id || `backend-${Date.now()}-${i}`,
          type: t.threat_type || t.type || 'unknown',
          name: t.name,
          startTime: t.parameters?.start_time ?? i * 35,
          duration: t.parameters?.duration ?? 30,
          severity: t.severity || 'medium',
          severityValue: t.parameters?.severity_value ?? 50,
          targetDevice: t.target_device || '',
          description: t.parameters?.description || '',
          parameters: t.parameters || {},
        }))
      }
      loaded.value = true
    } catch {
      loaded.value = true
    }
    loading.value = false
  }

  async function loadThreatTypes() {
    try {
      const resp = await api.getThreatTypes()
      threatTypes.value = Array.isArray(resp.data) ? resp.data : (resp.data?.types || [])
    } catch {}
  }

  function setEvents(newEvents: ThreatEvent[]) {
    events.value = newEvents
  }

  function markLoaded() {
    loaded.value = true
  }

  function clear() {
    events.value = []
    loaded.value = false
    syncStatus.value = 'idle'
  }

  return {
    events, loaded, loading, threatTypes, syncStatus,
    loadFromBackend, syncToBackend, loadThreatTypes,
    setEvents, markLoaded, clear,
  }
})
