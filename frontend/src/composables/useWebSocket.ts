import { ref, onUnmounted } from 'vue'

export function useWebSocket(channel: string) {
  const data = ref<any>(null)
  const connected = ref(false)
  const error = ref<string | null>(null)
  let ws: WebSocket | null = null
  let reconnectTimer: ReturnType<typeof setTimeout> | null = null
  let pingInterval: ReturnType<typeof setInterval> | null = null
  let missedPongs = 0

  function connect() {
    // Clear any pending reconnect
    if (reconnectTimer) { clearTimeout(reconnectTimer); reconnectTimer = null }

    const protocol = location.protocol === 'https:' ? 'wss:' : 'ws:'
    const url = `${protocol}//${location.host}/ws/${channel}`
    ws = new WebSocket(url)

    ws.onopen = () => {
      connected.value = true
      error.value = null
      missedPongs = 0
      startPing()
    }

    ws.onmessage = (event) => {
      try {
        const parsed = JSON.parse(event.data)
        // Track heartbeat/pong — reset missed counter
        if (parsed.event_type === 'heartbeat' || event.data === 'pong') {
          missedPongs = 0
          return
        }
        data.value = parsed
      } catch {
        if (event.data === 'pong') { missedPongs = 0; return }
        data.value = event.data
      }
    }

    ws.onclose = () => {
      connected.value = false
      stopPing()
      scheduleReconnect()
    }

    ws.onerror = () => {
      error.value = 'WebSocket error'
      connected.value = false
      stopPing()
      // Force close to trigger clean reconnect
      try { ws?.close() } catch { /* ignore */ }
    }
  }

  function scheduleReconnect() {
    if (reconnectTimer) return
    reconnectTimer = setTimeout(() => {
      reconnectTimer = null
      connect()
    }, 3000)
  }

  function startPing() {
    stopPing()
    missedPongs = 0
    // Send ping every 15s to keep the connection alive
    pingInterval = setInterval(() => {
      if (ws && ws.readyState === WebSocket.OPEN) {
        ws.send('ping')
        missedPongs++
        // If server hasn't responded to 3 consecutive pings (45s), reconnect
        if (missedPongs >= 3) {
          console.warn(`[WS:${channel}] No heartbeat for 45s, reconnecting...`)
          stopPing()
          try { ws?.close() } catch { /* ignore */ }
        }
      }
    }, 15000)
  }

  function stopPing() {
    if (pingInterval) { clearInterval(pingInterval); pingInterval = null }
  }

  function disconnect() {
    stopPing()
    if (reconnectTimer) { clearTimeout(reconnectTimer); reconnectTimer = null }
    if (ws) {
      ws.onclose = null
      ws.onerror = null
      ws.close()
      ws = null
    }
    connected.value = false
  }

  function send(msg: string) {
    if (ws && ws.readyState === WebSocket.OPEN) {
      ws.send(msg)
    }
  }

  connect()
  onUnmounted(disconnect)

  return { data, connected, error, send, disconnect }
}
