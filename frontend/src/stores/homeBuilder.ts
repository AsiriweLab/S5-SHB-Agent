import { defineStore } from 'pinia'
import { ref, watch } from 'vue'
import * as api from '@/services/apiClient'

export interface Room {
  id: string
  name: string
  type: string
  x: number
  y: number
  width: number
  height: number
  devices: Device[]
}

export interface Device {
  id: string
  name: string
  type: string
  icon: string
  x: number
  y: number
}

export interface Inhabitant {
  id: string
  name: string
  role: string
  icon: string
  age: number
  schedule: { wakeUp: number; sleep: number; homeTime: number; workFromHome: boolean }
  devicePreferences: string[]
}

export const useHomeBuilderStore = defineStore('homeBuilder', () => {
  // ── Persistent working state ──
  const rooms = ref<Room[]>([])
  const inhabitants = ref<Inhabitant[]>([])
  const loaded = ref(false)
  const loading = ref(false)

  // ── Metadata ──
  const home = ref<any>(null)
  const templates = ref<any[]>([])
  const deviceTypes = ref<any[]>([])

  // ── Auto-sync debounce ──
  let syncTimeout: ReturnType<typeof setTimeout> | null = null
  let isSyncing = false
  const syncStatus = ref<'idle' | 'syncing' | 'synced' | 'error'>('idle')

  function scheduleSyncToBackend() {
    if (isSyncing) return // Don't schedule if already syncing
    if (syncTimeout) clearTimeout(syncTimeout)
    syncTimeout = setTimeout(() => syncToBackend(), 1500)
  }

  // Watch for data changes and auto-sync (includes device additions/removals)
  watch(
    () => JSON.stringify({
      r: rooms.value.length,
      d: rooms.value.map(r => r.devices.length),
      dn: rooms.value.map(r => r.devices.map(d => d.id).join(',')),
      i: inhabitants.value.length,
    }),
    () => {
      if (loaded.value && rooms.value.length > 0) {
        scheduleSyncToBackend()
      }
    }
  )

  async function syncToBackend() {
    if (rooms.value.length === 0) return
    if (isSyncing) return // Prevent re-entry
    isSyncing = true
    syncStatus.value = 'syncing'
    try {
      // Create/update home
      await api.createHome({ home_name: 'My Smart Home' })

      // Delete existing devices first (before rooms, in case of FK constraints)
      const existingDevs = await api.getHomeDevices().catch(() => ({ data: [] }))
      const existingDevList = Array.isArray(existingDevs.data) ? existingDevs.data : (existingDevs.data?.devices || [])
      for (const ed of existingDevList) {
        await api.deleteDevice(ed.id).catch(() => {})
      }

      // Delete existing rooms
      const existingRooms = await api.getRooms().catch(() => ({ data: [] }))
      const existingRoomList = Array.isArray(existingRooms.data) ? existingRooms.data : []
      for (const er of existingRoomList) {
        await api.deleteRoom(er.id).catch(() => {})
      }

      // Add rooms and capture backend IDs, then add devices for each room
      for (const room of rooms.value) {
        const roomResp = await api.addRoom({
          name: room.name,
          room_type: room.type,
          area: room.width * room.height,
          floor: 0,
          x: room.x,
          y: room.y,
          width: room.width,
          height: room.height,
        } as any).catch(() => null)

        // Get the backend room ID from response
        const backendRoomId = roomResp?.data?.id || roomResp?.data?.room?.id
        if (backendRoomId && room.devices.length > 0) {
          for (const dev of room.devices) {
            await api.addDevice({
              name: dev.name,
              device_type: dev.type,
              room_id: backendRoomId,
              properties: { icon: dev.icon, x: dev.x, y: dev.y },
            }).catch(() => {})
          }
        }
      }

      // Sync residents
      const existingRes = await api.getResidents().catch(() => ({ data: [] }))
      const existingResList = Array.isArray(existingRes.data) ? existingRes.data : []
      for (const er of existingResList) {
        await api.deleteResident(er.id).catch(() => {})
      }
      for (const inh of inhabitants.value) {
        await api.addResident({
          name: inh.name,
          resident_type: inh.role,
          age: inh.age,
          icon: inh.icon,
          schedule: inh.schedule,
          device_preferences: inh.devicePreferences,
        } as any).catch(() => {})
      }

      syncStatus.value = 'synced'
    } catch {
      syncStatus.value = 'error'
    } finally {
      isSyncing = false
    }
  }

  async function loadFromBackend(force = false) {
    if (!force && loaded.value && rooms.value.length > 0) return // Already have data
    loading.value = true
    try {
      const homeResp = await api.getHome()
      if (!homeResp.data) { loading.value = false; loaded.value = true; return }
      home.value = homeResp.data

      const [roomsResp, devsResp, resResp] = await Promise.all([
        api.getRooms(), api.getHomeDevices(), api.getResidents()
      ])

      const roomsData = Array.isArray(roomsResp.data) ? roomsResp.data : (roomsResp.data?.rooms || [])
      const devsData = Array.isArray(devsResp.data) ? devsResp.data : (devsResp.data?.devices || [])
      const resData = Array.isArray(resResp.data) ? resResp.data : (resResp.data?.residents || [])

      if (roomsData.length > 0) {
        rooms.value = roomsData.map((r: any, i: number) => ({
          id: r.id,
          name: r.name,
          type: r.room_type || r.type || 'living_room',
          x: r.x ?? r.properties?.x ?? (50 + (i % 4) * 160),
          y: r.y ?? r.properties?.y ?? (50 + Math.floor(i / 4) * 130),
          width: r.width ?? r.properties?.width ?? 140,
          height: r.height ?? r.properties?.height ?? 100,
          devices: devsData
            .filter((d: any) => d.room_id === r.id)
            .map((d: any, di: number) => ({
              id: d.id,
              name: d.name,
              type: d.device_type || d.type,
              icon: d.properties?.icon || '',  // Will be resolved by the view
              x: d.properties?.x ?? (10 + (di % 3) * 40),
              y: d.properties?.y ?? (30 + Math.floor(di / 3) * 30),
            })),
        }))
      }

      if (resData.length > 0) {
        inhabitants.value = resData.map((r: any) => ({
          id: r.id,
          name: r.name,
          role: r.resident_type || r.role || 'adult',
          icon: r.icon || '',  // Will be resolved by the view
          age: r.age || 35,
          schedule: r.schedule || { wakeUp: 7, sleep: 23, homeTime: 12, workFromHome: false },
          devicePreferences: r.device_preferences || r.devicePreferences || [],
        }))
      }

      loaded.value = true
    } catch {
      loaded.value = true // Mark as loaded even on error so we don't keep retrying
    }
    loading.value = false
  }

  async function loadTemplates() {
    try {
      const resp = await api.getTemplates()
      templates.value = Array.isArray(resp.data) ? resp.data : (resp.data.templates || [])
    } catch {}
  }

  async function loadDeviceTypes() {
    try {
      const resp = await api.getDeviceTypes()
      deviceTypes.value = resp.data
    } catch {}
  }

  function setRooms(newRooms: Room[]) {
    rooms.value = newRooms
  }

  function setInhabitants(newInhabitants: Inhabitant[]) {
    inhabitants.value = newInhabitants
  }

  function markLoaded() {
    loaded.value = true
  }

  function clear() {
    rooms.value = []
    inhabitants.value = []
    loaded.value = false
    home.value = null
    syncStatus.value = 'idle'
  }

  return {
    rooms, inhabitants, loaded, loading, home, templates, deviceTypes, syncStatus,
    loadFromBackend, syncToBackend, loadTemplates, loadDeviceTypes,
    setRooms, setInhabitants, markLoaded, clear,
  }
})
