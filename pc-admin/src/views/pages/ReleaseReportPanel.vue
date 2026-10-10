<script setup lang="ts">
import { computed } from 'vue'
import type { ReleaseReport } from './release-report'
const props = defineProps<{ report: ReleaseReport | null; stale: boolean; loading: boolean; error: string; disabled?: boolean }>()
const emit = defineEmits<{ refresh: [] }>()
const current = computed(() => !props.stale && props.report ? props.report : null)
const hasChanges = computed(() => current.value && (current.value.diff.addedComponentIds.length || current.value.diff.removedComponentIds.length || current.value.diff.updatedComponentIds.length || current.value.diff.orderChanged || current.value.diff.themeChanged || current.value.diff.metadataChanged || current.value.diff.nameChanged))
function refresh() { if (!props.disabled && !props.loading) emit('refresh') }
</script>
<template>
  <section class="release-report-panel" aria-label="页面发布检查">
    <div class="release-report-heading"><strong>发布差异与引用检查</strong><button type="button" class="secondary-button" :disabled="disabled || loading" @click="refresh">{{ loading ? '正在检查…' : '检查发布差异与引用' }}</button></div>
    <p class="help-text">对照当前线上版本，检查组件变更、引用有效性和小程序兼容状态。检查不保存新的发布版本，也不自动发布。</p>
    <p v-if="error" role="alert" class="error">{{ error }}</p>
    <p v-if="loading" role="status">正在读取已保存草稿与线上版本的检查结果…</p>
    <p v-else-if="!current" role="status">{{ report && stale ? '报告已过期，草稿或线上版本发生变化，请重新检查。' : '尚未检查当前草稿。' }}</p>
    <div v-else class="release-report-result">
      <p :class="current.canPublish ? 'release-report-good' : 'release-report-blocked'" role="status">{{ current.canPublish ? '当前检查未发现发布阻断项' : '当前存在发布阻断项，请处理后重新检查' }} · 草稿修订 {{ current.revision }} · 发布状态修订 {{ current.publicationRevision }}</p>
      <p>页面配置版本 {{ current.schemaVersion }} · 小程序支持版本 {{ current.runtimeSchemaVersion }} · {{ current.runtimeSupported ? '兼容' : '当前小程序不兼容' }}</p>
      <p v-if="!current.publishedVersionId">页面尚未发布，此次将建立首次线上版本。</p>
      <ul v-if="hasChanges" class="release-report-diff">
        <li v-if="current.diff.addedComponentIds.length">新增 {{ current.diff.addedComponentIds.length }} 个组件</li>
        <li v-if="current.diff.removedComponentIds.length">移除 {{ current.diff.removedComponentIds.length }} 个组件</li>
        <li v-if="current.diff.updatedComponentIds.length">修改 {{ current.diff.updatedComponentIds.length }} 个组件</li>
        <li v-if="current.diff.nameChanged">页面名称变化</li><li v-if="current.diff.orderChanged">组件顺序变化</li><li v-if="current.diff.themeChanged">页面主题变化</li><li v-if="current.diff.metadataChanged">标签或分享资料变化</li>
      </ul><p v-else>与当前线上内容一致。</p>
      <ul v-if="current.issues.length" class="release-report-issues"><li v-for="(issue, index) in current.issues" :key="index"><strong>{{ issue.severity === 'ERROR' ? '阻断' : '提醒' }}</strong> {{ issue.message }}<small>{{ issue.componentId ? `组件 ${issue.componentId} · ` : '' }}{{ issue.path }}</small></li></ul><p v-else>素材、站内链接和业务引用检查通过。</p>
      <p class="help-text">报告只反映本次读取状态；发布时服务端仍会重新校验权限、修订号、引用和运行时能力。</p>
    </div>
  </section>
</template>
<style scoped>.release-report-panel{margin:16px 0;padding:16px 20px;border:1px solid var(--mall-color-border);border-radius:12px;background:var(--mall-color-surface)}.release-report-heading{display:flex;flex-wrap:wrap;gap:12px;justify-content:space-between;align-items:center}.release-report-panel p{line-height:1.6;font-size:13px}.release-report-good{color:#245739}.release-report-blocked{color:#A3202B}.release-report-diff,.release-report-issues{padding-left:20px;font-size:13px;line-height:1.7}.release-report-issues small{display:block;color:var(--mall-color-muted);overflow-wrap:anywhere}.release-report-issues strong{color:#A3202B}</style>
