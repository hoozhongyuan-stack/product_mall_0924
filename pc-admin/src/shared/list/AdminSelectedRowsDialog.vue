<script setup lang="ts">
import { ElDialog } from 'element-plus'
defineProps<{modelValue: boolean; title: string; columns: {key: string; label: string}[]; rows: Record<string, unknown>[]}>()
defineEmits<{'update:modelValue': [value: boolean]}>()
function cell(row: Record<string, unknown>, key: string) { const value = row[key]; return value === null || value === undefined || value === '' ? '—' : String(value) }
</script>
<template><ElDialog v-if="modelValue" :model-value="modelValue" :title="title" width="960px" :close-on-click-modal="false" @update:model-value="$emit('update:modelValue', $event)"><p>当前页所选 {{ rows.length }} 条记录。此窗口仅用于核对信息。</p><div class="table-wrap" tabindex="0" aria-label="所选记录，可横向滚动"><table><thead><tr><th v-for="column in columns" :key="column.key" scope="col">{{ column.label }}</th></tr></thead><tbody><tr v-for="(row,index) in rows" :key="index"><td v-for="column in columns" :key="column.key">{{ cell(row,column.key) }}</td></tr></tbody></table><p v-if="!rows.length" class="empty-state">暂无所选记录。</p></div><template #footer><button class="secondary-button" @click="$emit('update:modelValue', false)">关闭</button></template></ElDialog></template>
