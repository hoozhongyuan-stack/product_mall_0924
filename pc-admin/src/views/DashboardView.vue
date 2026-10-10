<script setup lang="ts">
import { computed } from 'vue'
import { useRouter } from 'vue-router'
import type { Account } from '../api'
import { authorizedSections } from '../navigation'
import BusinessSummaryView from './BusinessSummaryView.vue'
const props = defineProps<{ account: Account }>()
const router = useRouter()
const sections = computed(() => authorizedSections(router, props.account.permissionCodes)
  .map(section => ({ ...section, links: section.links.filter(link => link.path !== '/') }))
  .filter(section => section.links.length))
const commonPaths = ['/orders', '/catalog', '/inventory', '/coupons', '/pages/micro', '/aftersales']
const commonTasks = computed(() => sections.value.flatMap(section => section.links)
  .filter(link => commonPaths.includes(link.path))
  .sort((a, b) => commonPaths.indexOf(a.path) - commonPaths.indexOf(b.path)))
const canReadReport = computed(() => props.account.permissionCodes.includes('business.report.read'))
</script>

<template>
  <section class="page-content dashboard">
    <header class="page-heading"><div><h1>工作台</h1><p>{{ account.displayName }}，欢迎回来。选择业务入口，继续处理店铺工作。</p></div><span class="badge badge-good">账号已启用</span></header>
    <section class="dashboard-common panel" aria-label="常用工作">
      <h2>常用工作</h2><p>先处理店铺日常工作，再查看经营明细。</p>
      <div v-if="commonTasks.length"><RouterLink v-for="link in commonTasks" :key="link.path" :to="link.path">{{ link.title }}<span aria-hidden="true">↗</span></RouterLink></div>
      <p v-else class="empty-state">暂无常用业务权限，可在下方查看全部入口。</p>
    </section>
    <BusinessSummaryView v-if="canReadReport" :key="account.accountId" :account="account" embedded />
    <details class="dashboard-links">
      <summary>全部业务入口</summary>
      <p v-if="!sections.length" class="empty-state">当前没有业务操作权限，请联系主账号分配权限组。</p>
      <div v-else class="panel dashboard-entry-list">
        <div v-for="section in sections" :key="section.name" class="dashboard-entry-row">
          <h3>{{ section.name }}</h3>
          <div><RouterLink v-for="link in section.links" :key="link.path" :to="link.path">{{ link.title }}<span aria-hidden="true">↗</span></RouterLink></div>
        </div>
      </div>
    </details>
  </section>
</template>

<style scoped>
.dashboard-common{padding:20px 24px;margin-bottom:24px}.dashboard-common h2{font-size:20px;margin:0 0 8px}.dashboard-common p{color:var(--mall-color-muted);font-size:14px}.dashboard-common>div{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:12px}.dashboard-common a{display:flex;align-items:center;justify-content:space-between;gap:12px;padding:16px;border:1px solid var(--mall-color-border);border-radius:var(--mall-radius-control);text-decoration:none;color:var(--mall-color-text)}.dashboard-common a:hover{color:var(--mall-color-brand);background:var(--mall-color-brand-soft)}.dashboard-common a:focus-visible,.dashboard-links summary:focus-visible{outline:2px solid var(--mall-color-focus);outline-offset:3px}.dashboard-links summary{cursor:pointer;font-size:20px;font-weight:600;padding:16px 0}.dashboard-links[open] summary{margin-bottom:8px}@media(max-width:600px){.dashboard-common{padding:16px}.dashboard-common>div{grid-template-columns:repeat(2,minmax(0,1fr))}.dashboard-common a{padding:12px;overflow-wrap:anywhere}}
</style>
