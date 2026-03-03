import { createRouter, createWebHistory } from 'vue-router'

const routes = [
  { path: '/', name: 'Dashboard', component: () => import('@/views/DashboardView.vue') },
  { path: '/home-builder', name: 'HomeBuilder', component: () => import('@/views/HomeBuilderView.vue') },
  { path: '/threats', name: 'ThreatBuilder', component: () => import('@/views/ThreatBuilderView.vue') },
  { path: '/simulation', name: 'Simulation', component: () => import('@/views/SimulationView.vue') },
  { path: '/sessions', name: 'Sessions', component: () => import('@/views/SessionsView.vue') },
  { path: '/blockchain', name: 'Blockchain', component: () => import('@/views/BlockchainView.vue') },
  { path: '/agents', name: 'Agents', component: () => import('@/views/AgentMonitorView.vue') },
  { path: '/chat', name: 'Chat', component: () => import('@/views/NLUChatView.vue') },
  { path: '/anomaly', name: 'Anomaly', component: () => import('@/views/AnomalyView.vue') },
  { path: '/governance', name: 'Governance', component: () => import('@/views/GovernanceView.vue') },
  { path: '/audit', name: 'Audit', component: () => import('@/views/AuditView.vue') },
]

const router = createRouter({
  history: createWebHistory(),
  routes,
})

export default router
