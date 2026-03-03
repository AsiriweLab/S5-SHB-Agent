import axios from 'axios'

const api = axios.create({
  baseURL: '/api',
  timeout: 30000,
  headers: { 'Content-Type': 'application/json' },
})

// Health
export const getHealth = () => api.get('/health')
export const getHealthDetail = () => api.get('/health/detail')
export const getHealthS5HES = () => api.get('/health/s5-hes')
export const getHealthMCP = () => api.get('/health/mcp')

// Home Builder
export const getHome = () => api.get('/home/')
export const createHome = (data: { template?: string; home_name?: string }) => api.post('/home/', data)
export const updateHome = (data: { home_name?: string }) => api.put('/home/', data)
export const getTemplates = () => api.get('/home/templates')
export const getDeviceTypes = () => api.get('/home/device-types')
export const getDeviceTypesCategorized = () => api.get('/home/device-types/categorized')
export const getRooms = () => api.get('/home/rooms')
export const addRoom = (data: { name: string; room_type: string; area?: number; floor?: number }) => api.post('/home/rooms', data)
export const updateRoom = (id: string, data: any) => api.put(`/home/rooms/${id}`, data)
export const deleteRoom = (id: string) => api.delete(`/home/rooms/${id}`)
export const getHomeDevices = () => api.get('/home/devices')
export const addDevice = (data: { name: string; device_type: string; room_id: string; properties?: any }) => api.post('/home/devices', data)
export const deleteDevice = (id: string) => api.delete(`/home/devices/${id}`)
export const getResidents = () => api.get('/home/residents')
export const addResident = (data: { name: string; resident_type?: string; schedule?: any }) => api.post('/home/residents', data)
export const deleteResident = (id: string) => api.delete(`/home/residents/${id}`)

// Threats
export const getThreatTypes = () => api.get('/threats/types')
export const getThreats = () => api.get('/threats')
export const addThreat = (data: any) => api.post('/threats', data)
export const updateThreat = (id: string, data: any) => api.put(`/threats/${id}`, data)
export const deleteThreat = (id: string) => api.delete(`/threats/${id}`)

// Simulation
export const getSimulationStatus = () => api.get('/simulation/status')
export const startSimulation = (data: { duration_hours?: number; time_compression?: number; include_threats?: boolean }) => api.post('/simulation/start', data)
export const pauseSimulation = () => api.post('/simulation/pause')
export const resumeSimulation = () => api.post('/simulation/resume')
export const stopSimulation = () => api.post('/simulation/stop')
export const getSimulationEvents = (params?: any) => api.get('/simulation/events', { params })
export const getDeviceTelemetry = (deviceId: string) => api.get(`/simulation/telemetry/${deviceId}`)

// Sessions
export const listSessions = () => api.get('/sessions/')
export const createSession = (data: { name: string; preset?: string; device_mode?: string; real_devices?: any[] }) => api.post('/sessions/', data)
export const getSession = (name: string) => api.get(`/sessions/${name}`)
export const resumeSession = (name: string) => api.post(`/sessions/${name}/resume`)
export const saveSession = (name: string) => api.post(`/sessions/${name}/save`)
export const deleteSession = (name: string) => api.delete(`/sessions/${name}`)
export const getActiveSession = () => api.get('/sessions/active')
export const teardownSession = () => api.post('/sessions/teardown')

// Devices (session)
export const getDevices = () => api.get('/devices')
export const getDeviceStatus = (id: string) => api.get(`/devices/${id}`)
export const getAllTelemetry = () => api.get('/devices/telemetry')
export const executeCommand = (id: string, data: any) => api.post(`/devices/${id}/command`, data)
export const scanEmergencies = () => api.get('/devices/emergencies')
export const getAgentMapping = () => api.get('/devices/agent-mapping')
export const getDeviceMode = () => api.get('/devices/config/mode')
export const getProtocols = () => api.get('/devices/config/protocols')
export const testDeviceConnection = (data: any) => api.post('/devices/config/test-connection', data)

// Blockchain
export const getBlockchainSummary = () => api.get('/blockchain/summary')
export const getBlocks = (params?: any) => api.get('/blockchain/blocks', { params })
export const getLatestBlock = () => api.get('/blockchain/blocks/latest')
export const getBlock = (index: number) => api.get(`/blockchain/blocks/${index}`)
export const getTransactions = (params?: any) => api.get('/blockchain/transactions', { params })
export const getTransaction = (hash: string) => api.get(`/blockchain/transactions/${hash}`)
export const getRegisteredAgents = () => api.get('/blockchain/agents')
export const getPermissions = () => api.get('/blockchain/permissions')
export const getPriorities = () => api.get('/blockchain/priorities')
export const getConflicts = () => api.get('/blockchain/conflicts')
export const getAdaptivePow = () => api.get('/blockchain/adaptive-pow')

// Agents
export const getAgents = () => api.get('/agents')
export const getAgent = (id: string) => api.get(`/agents/${id}`)
export const runAgentCycle = (duration: number = 0, interval: number = 60) =>
  api.post(`/agents/run-cycle?duration=${duration}&interval=${interval}`)
export const runAgentsParallel = (duration: number = 0, interval: number = 60) =>
  api.post(`/agents/run-parallel?duration=${duration}&interval=${interval}`)
export const getAgentFeedback = (id: string) => api.get(`/agents/${id}/feedback`)
export const getAgentDecisions = (id: string) => api.get(`/agents/${id}/decisions`)
export const startAutoRun = (interval: number = 60, duration: number = 0) =>
  api.post(`/agents/auto-run/start?interval=${interval}&duration=${duration}`)
export const stopAutoRun = () => api.post('/agents/auto-run/stop')
export const getAutoRunStatus = () => api.get('/agents/auto-run/status')
export const getActivityHistory = (limit: number = 200) => api.get(`/agents/activity-history?limit=${limit}`)

// NLU
export const sendNLUCommand = (data: { text: string }) => api.post('/nlu/command', data)
export const getNLUHistory = () => api.get('/nlu/history')
export const getNLUStats = () => api.get('/nlu/stats')

// Anomaly
export const trainAnomaly = () => api.post('/anomaly/train')
export const detectAnomaly = () => api.post('/anomaly/detect')
export const getAnomalyStats = () => api.get('/anomaly/stats')
export const getAnomalyModels = () => api.get('/anomaly/models')
export const accumulateTelemetry = () => api.post('/anomaly/accumulate')

// Governance
export const getPreferences = () => api.get('/governance/preferences')
export const updatePreference = (key: string, data: any) => api.put(`/governance/preferences/${key}`, data)
export const getLockedParams = () => api.get('/governance/locked')
export const getPresets = () => api.get('/governance/presets')
export const applyPreset = (name: string) => api.post(`/governance/presets/${name}/apply`)
export const getModelAssignments = () => api.get('/governance/models')
export const updateModelAssignment = (agentId: string, data: any) => api.put(`/governance/models/${agentId}`, data)
export const getModelRegistry = () => api.get('/governance/registry')
export const getCostTracking = () => api.get('/governance/cost')
export const getGovernanceLog = () => api.get('/governance/log')

// Offchain
export const getOffchainStats = () => api.get('/offchain/stats')

// Audit
export const runAudit = () => api.post('/audit/run')
export const getAuditResults = () => api.get('/audit/results')

// Report
export const getReport = () => api.get('/report')
export const getReportSection = (section: string) => api.get(`/report/${section}`)

// Scenarios
export const getScenarios = () => api.get('/scenarios')
export const runAllScenarios = () => api.post('/scenarios/run-all')
export const runScenario = (id: number) => api.post(`/scenarios/${id}/run`)
export const getScenario = (id: number) => api.get(`/scenarios/${id}`)
export const getScenarioCategories = () => api.get('/scenarios/categories')

export default api
