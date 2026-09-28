<script setup lang="ts">
import { onMounted, ref } from 'vue'
import { api, type AuditEntry } from '../api'

const logs = ref<AuditEntry[]>([])
const loading = ref(true)
const error = ref('')
async function load() {
  loading.value = true
  error.value = ''
  try {
    logs.value = await api<AuditEntry[]>('/audit-logs')
  } catch (reason) {
    error.value = reason instanceof Error ? reason.message : '日志加载失败。'
  } finally {
    loading.value = false
  }
}
onMounted(load)

function timestamp(value: string) {
  return new Date(value).toLocaleString('zh-CN', { hour12: false })
}
</script>

<template>
  <section class="page-content">
    <div class="page-heading"><div><h1>操作日志</h1><p>最近 100 条后台操作记录，按时间倒序显示。</p></div>
      <button class="secondary-button" type="button" :disabled="loading" @click="load">刷新</button>
    </div>
    <p v-if="error" class="error notice" role="alert">{{ error }}</p>
    <p v-if="loading" class="loading-inline" role="status">正在加载日志…</p>
    <div v-else class="panel table-wrap">
      <table><thead><tr><th>时间</th><th>操作</th><th>结果</th><th>操作者 ID</th><th>对象</th><th>详情</th></tr></thead>
        <tbody><tr v-for="item in logs" :key="item.id">
          <td>{{ timestamp(item.occurredAt) }}</td><td class="strong-cell">{{ item.actionCode }}</td>
          <td><span :class="['badge', item.result === 'SUCCESS' ? 'badge-good' : 'badge-muted']">{{ item.result }}</span></td>
          <td class="code">{{ item.actorId || '未识别' }}</td>
          <td>{{ item.objectType }}<br /><span class="code">{{ item.objectId || '—' }}</span></td>
          <td><details><summary>查看</summary><pre>{{ JSON.stringify({ before: item.before, after: item.after, requestId: item.requestId }, null, 2) }}</pre></details></td>
        </tr></tbody></table>
      <p v-if="!logs.length" class="empty-state">暂无操作日志。</p>
    </div>
    <button v-if="error && !loading" class="text-button retry" type="button" @click="load">重新加载</button>
  </section>
</template>
