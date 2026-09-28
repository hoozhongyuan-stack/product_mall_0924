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
const canReadReport = computed(() => props.account.permissionCodes.includes('business.report.read'))
</script>

<template>
  <section class="page-content dashboard">
    <header class="page-heading"><div><h1>工作台</h1><p>{{ account.displayName }}，欢迎回来。选择业务入口，继续处理店铺工作。</p></div><span class="badge badge-good">账号已启用</span></header>
    <BusinessSummaryView v-if="canReadReport" :key="account.accountId" :account="account" embedded />
    <section class="dashboard-links" aria-labelledby="business-entry-title">
      <h2 id="business-entry-title">业务入口</h2>
      <p v-if="!sections.length" class="empty-state">当前没有业务操作权限，请联系主账号分配权限组。</p>
      <div v-else class="panel dashboard-entry-list">
        <div v-for="section in sections" :key="section.name" class="dashboard-entry-row">
          <h3>{{ section.name }}</h3>
          <div><RouterLink v-for="link in section.links" :key="link.path" :to="link.path">{{ link.title }}<span aria-hidden="true">↗</span></RouterLink></div>
        </div>
      </div>
    </section>
  </section>
</template>
