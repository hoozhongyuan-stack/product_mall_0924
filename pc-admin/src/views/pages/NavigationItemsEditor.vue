<script setup lang="ts">
import { onMounted, onUnmounted, ref } from 'vue'
import { ElButton } from 'element-plus'
import { api } from '../../api'
import AssetPicker from '../../shared/AssetPicker.vue'
import { appendNavigationItem, moveNavigationItem, removeNavigationItem, updateNavigationItem, type NavigationConfig, type NavigationItem, type NavigationTarget } from '../../shared/singleton-config.mjs'
import type { MicroPageList, MicroPageSummary } from './types'
const props = defineProps<{ config: NavigationConfig; disabled: boolean; canEdit: boolean }>()
const emit = defineEmits<{ change: [config: NavigationConfig] }>()
const pages = ref<MicroPageSummary[]>([]), targetError = ref(''), targetsLoading = ref(false), truncated = ref(false)
const failed = ref<string[]>([])
const targetQuery = ref(''), appliedQuery = ref('')
let generation = 0
const targets: [NavigationTarget['type'], string][] = [['HOME', '首页（跟随当前首页）'], ['MICRO', '微页面'], ['CATEGORY', '分类页'], ['CART', '购物车'], ['ME', '我的']]
const assetUrl = (id: string) => `/api/v1/admin/assets/${encodeURIComponent(id)}/file`
function mediaFailed(id: string) { if (!failed.value.includes(id)) failed.value = [...failed.value, id] }
async function loadTargets() {
  const current = ++generation
  targetError.value = ''; targetsLoading.value = true
  try {
    const result = await api<MicroPageList>(`/pages?includeHome=1&page=1&pageSize=50${appliedQuery.value?`&q=${encodeURIComponent(appliedQuery.value)}`:''}`)
    if (current !== generation) return
    if (!Array.isArray(result.rows)) throw new Error('微页面列表格式不正确。')
    pages.value = result.rows; truncated.value = result.total > result.rows.length
  } catch (reason) { if (current === generation) targetError.value = reason instanceof Error ? reason.message : '读取微页面失败。' }
  finally { if (current === generation) targetsLoading.value = false }
}
function searchTargets() { appliedQuery.value=targetQuery.value.trim(); void loadTargets() }
function resetTargets() { targetQuery.value=''; appliedQuery.value=''; void loadTargets() }
onMounted(loadTargets)
onUnmounted(() => { generation++ })
function change(config: NavigationConfig) { if (props.canEdit && !props.disabled) emit('change', config) }
function addItem() { change(appendNavigationItem(props.config, `nav_${crypto.randomUUID().replaceAll('-', '').slice(0,24)}`)) }
function patch(item: NavigationItem, value: Partial<NavigationItem>) { change(updateNavigationItem(props.config, item.key, value)) }
function setTarget(item: NavigationItem, type: NavigationTarget['type']) { patch(item, { target: type === 'MICRO' ? { type, pageId: '' } : { type } }) }
</script>
<template>
<section class="singleton-editor" aria-labelledby="navigation-editor-title">
  <div class="singleton-section-head"><h2 id="navigation-editor-title">导航入口 · {{ config.items.length }}/5</h2><p>配置 1 至 5 项，名称最多 10 字；微页面可重复作为不同入口。草稿可关联未发布页面，发布前必须先发布目标页面。</p></div>
  <form class="navigation-target-search" @submit.prevent="searchTargets"><label>查找微页面<input v-model="targetQuery" maxlength="80" placeholder="输入页面名称" :disabled="targetsLoading" /></label><ElButton native-type="submit" :disabled="targetsLoading">查询页面</ElButton><ElButton :disabled="targetsLoading" @click="resetTargets">重置</ElButton></form>
  <p v-if="targetsLoading" role="status">正在读取微页面…</p>
  <p v-if="targetError" class="singleton-error" role="alert">{{ targetError }} <el-button text :disabled="targetsLoading" @click="loadTargets">重试读取页面</el-button></p>
  <p v-if="truncated" class="help-text">列表显示前 50 项，可按名称查找其他微页面。</p>
  <div class="singleton-nav-grid"><article v-for="(item,index) in config.items" :key="item.key" class="singleton-nav-item">
    <div class="singleton-nav-title"><strong>导航 {{ index + 1 }}</strong></div>
    <label class="navigation-field">导航名称<input :aria-label="`导航名称 ${index+1}`" :value="item.label" maxlength="10" :disabled="disabled || !canEdit" @input="patch(item, { label: ($event.target as HTMLInputElement).value })" /></label>
    <label class="navigation-field">关联页面<select :aria-label="`关联页面 ${index+1}`" :value="item.target?.type" :disabled="disabled || !canEdit" @change="setTarget(item, ($event.target as HTMLSelectElement).value as NavigationTarget['type'])"><option v-for="[type,label] in targets" :key="type" :value="type">{{ label }}</option></select></label>
    <template v-if="item.target?.type==='MICRO'"><label class="navigation-field">选择微页面<select :aria-label="`微页面 ${index+1}`" :value="item.target.pageId" :disabled="disabled || !canEdit" @change="patch(item, {target:{type:'MICRO',pageId:($event.target as HTMLSelectElement).value}})"><option value="">请选择微页面</option><option v-if="item.target.pageId && !pages.some(page=>page.pageId===item.target?.pageId)" :value="item.target.pageId">已关联页面（可搜索名称重新选择）</option><option v-for="page in pages" :key="page.pageId" :value="page.pageId">{{ page.name }}{{ page.publishedRevision===null?'（未发布）':'' }}</option></select></label></template>
    <div class="singleton-icon-pair"><div v-for="[role,label] in ([['iconAssetId','未选中图标'],['selectedIconAssetId','选中图标']] as const)" :key="role"><span>{{ label }}</span><img v-if="item[role] && !failed.includes(item[role]!)" :src="assetUrl(item[role]!)" :alt="`${item.label}${label}`" @error="mediaFailed(item[role]!)" /><span v-else class="singleton-icon-empty">{{item[role]?'图片失效':'默认'}}</span><AssetPicker v-if="canEdit" kind="IMAGE" square :disabled="disabled" :target-key="JSON.stringify([item.key,role,item[role]])" @select="patch(item,{[role]:$event.assetId})" /></div></div>
    <div v-if="canEdit" class="navigation-order"><el-button text :disabled="disabled || index===0" :aria-label="`上移导航 ${index+1}`" @click="change(moveNavigationItem(config,item.key,-1))">上移</el-button><el-button text :disabled="disabled || index===config.items.length-1" :aria-label="`下移导航 ${index+1}`" @click="change(moveNavigationItem(config,item.key,1))">下移</el-button><el-button text type="danger" :disabled="disabled || config.items.length===1" @click="change(removeNavigationItem(config,item.key))">删除</el-button><el-button v-if="item.iconAssetId || item.selectedIconAssetId" text :disabled="disabled" @click="patch(item,{iconAssetId:null,selectedIconAssetId:null})">恢复默认图标</el-button></div>
  </article></div>
  <el-button v-if="canEdit" data-add-navigation :disabled="disabled || config.items.length>=5" @click="addItem">添加导航</el-button>
</section>
</template>
<style scoped>
.navigation-target-search{display:flex;align-items:end;gap:8px;flex-wrap:wrap;margin:16px 0}.navigation-target-search label{display:grid;gap:6px;min-width:0}.navigation-target-search input{min-width:0;min-height:36px;padding:6px 8px;border:1px solid var(--mall-color-border);border-radius:6px}.navigation-field{display:grid;gap:6px;margin-top:12px;font-size:13px}.navigation-field input,.navigation-field select{width:100%;min-width:0;min-height:36px;padding:6px 8px;border:1px solid var(--mall-color-border);border-radius:6px;background:var(--mall-color-surface)}.navigation-order{display:flex;flex-wrap:wrap;gap:4px}.singleton-nav-grid{grid-template-columns:repeat(auto-fit,minmax(min(100%,240px),1fr));margin-bottom:16px}
</style>
