<script setup lang="ts">
import { onMounted, onUnmounted, ref } from 'vue'
import { api } from '../../api'
import { cloneComponents, replaceWithTemplate, themePresets, validateReuseData, type PageCombination, type PageReuseData, type PageTemplate } from './page-reuse'
import type { PageComponent, PageConfig } from './types'
const props = defineProps<{ config: PageConfig; disabled?: boolean }>()
const emit = defineEmits<{ replace: [value: PageConfig]; append: [value: PageComponent[]] }>()
const data = ref<PageReuseData>({ templates: [], combinations: [] })
const loading = ref(false), error = ref('')
const selected = ref<PageTemplate | null>(null)
let sequence = 0
async function load() {
  const current = ++sequence; loading.value = true; error.value = ''
  try { const result = validateReuseData(await api<PageReuseData>('/pages/templates')); if (current === sequence) data.value = result }
  catch (reason) { if (current === sequence) error.value = reason instanceof Error ? reason.message : '模板暂不可用。' }
  finally { if (current === sequence) loading.value = false }
}
function choose(template: PageTemplate) { if (!props.disabled) selected.value = template }
function replace() {
  if (!selected.value || props.disabled) return
  emit('replace', replaceWithTemplate(props.config, selected.value)); selected.value = null
}
function append(combination: PageCombination) {
  if (props.disabled) return
  if (props.config.components.length + combination.components.length > 40) { error.value = '组合加入后超过 40 个组件，请先移除部分组件。'; return }
  emit('append', cloneComponents(combination.components, props.config.components.length + 1))
}
function preset(index: number) { if (!props.disabled) emit('replace', { ...props.config, theme: { ...themePresets[index]!.theme } }) }
onMounted(() => { void load() }); onUnmounted(() => { sequence++ })
</script>
<template>
  <details class="page-reuse-panel"><summary>主题、页面模板与组件组合</summary>
    <p class="help-text">模板和组合生成独立草稿内容，后续不会自动同步模板。保存草稿后仍需预览、校验和发布。</p>
    <div class="page-reuse-presets"><button v-for="(theme, index) in themePresets" :key="theme.id" type="button" :disabled="disabled" @click="preset(index)">{{ theme.name }}</button></div>
    <p v-if="loading" role="status">正在读取模板…</p><p v-if="error" role="alert">{{ error }} <button type="button" :disabled="loading" @click="load">重试读取模板</button></p>
    <div class="page-reuse-options"><div v-for="template in data.templates" :key="template.templateId"><button type="button" :disabled="disabled" @click="choose(template)">应用{{ template.name }}模板</button><small>{{ template.description }}</small></div></div>
    <div class="page-reuse-options"><div v-for="combination in data.combinations" :key="combination.combinationId"><button type="button" :disabled="disabled || config.components.length + combination.components.length > 40" @click="append(combination)">加入{{ combination.name }}组合</button><small>{{ combination.description }}</small></div></div>
    <div v-if="selected" role="alertdialog" aria-label="确认替换页面模板" class="page-reuse-confirm"><strong>使用{{ selected.name }}？</strong><p>将替换所有当前组件和主题，保留页面身份、分享资料和发布状态。模板图片和商品需要重新配置。确认后须保存草稿，不会自动发布。</p><button type="button" :disabled="disabled" @click="replace">确认替换草稿</button><button type="button" @click="selected = null">取消</button></div>
  </details>
</template>
<style scoped>
.page-reuse-panel{padding:12px 16px;border:1px solid var(--mall-color-border);border-radius:12px;margin:12px 0}.page-reuse-panel summary{cursor:pointer;font-weight:600}.page-reuse-presets,.page-reuse-options{display:flex;flex-wrap:wrap;gap:10px;margin:12px 0}.page-reuse-options>div{display:grid;gap:6px;max-width:220px}.page-reuse-panel button{border:1px solid var(--mall-color-border);border-radius:8px;padding:8px 12px;background:var(--mall-color-surface);color:var(--mall-color-text);cursor:pointer}.page-reuse-options small{color:var(--mall-color-muted)}.page-reuse-confirm{padding:16px;background:var(--mall-color-canvas-admin);border-radius:10px}.page-reuse-confirm p{line-height:1.6}
</style>
