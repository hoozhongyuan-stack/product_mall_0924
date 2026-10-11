<script setup lang="ts">
import { ElDialog } from 'element-plus'
defineProps<{ title: string; busy?: boolean; wide?: boolean }>()
const emit = defineEmits<{ close: [] }>()
</script>

<template>
  <ElDialog :model-value="true" :title="title" :width="wide ? '1120px' : '760px'"
    class="catalog-operation-dialog" :close-on-click-modal="false" :close-on-press-escape="!busy"
    :show-close="!busy" :before-close="() => { if (!busy) emit('close') }">
    <slot />
    <template v-if="$slots.footer" #footer><slot name="footer" /></template>
  </ElDialog>
</template>

<style>
.catalog-operation-dialog .catalog-editor-workspace { max-width: none; }
.catalog-operation-dialog .catalog-workspace-heading { padding-bottom: 16px; align-items: start; }
.catalog-operation-dialog .catalog-workspace-heading h1 { font-size: 18px; }
.catalog-operation-dialog .catalog-workspace-heading .catalog-back { display: none; }
.catalog-operation-dialog .catalog-editor { padding: 16px; }
.catalog-operation-dialog .catalog-batch-preview,
.catalog-operation-dialog .catalog-batch-result { margin: 0; padding: 0; border: 0; }
.catalog-operation-dialog .catalog-batch-preview > .primary-button,
.catalog-operation-dialog .catalog-batch-preview > .secondary-button,
.catalog-operation-dialog .catalog-editor > button[type="submit"] { display: none; }
.catalog-operation-dialog .dialog-footer { display: flex; gap: 12px; justify-content: flex-end; }
.catalog-operation-dialog .catalog-grade-list { display: flex; flex-wrap: wrap; gap: 8px; }
</style>
