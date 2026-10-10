<script setup lang="ts">
defineProps<{ disabled: boolean; canUndo: boolean; canRedo: boolean; canCopy: boolean; count: number }>()
defineEmits<{ undo: []; redo: []; copy: []; theme: [] }>()
</script>
<template>
  <div class="editor-local-actions" aria-label="编辑操作">
    <button type="button" class="secondary-button" :disabled="disabled || !canUndo" @click="$emit('undo')">撤销</button>
    <button type="button" class="secondary-button" :disabled="disabled || !canRedo" @click="$emit('redo')">重做</button>
    <button type="button" class="secondary-button" :disabled="disabled || !canCopy || count >= 40" @click="$emit('copy')">复制组件</button>
    <button type="button" class="text-button" @click="$emit('theme')">页面主题</button>
    <small>拖动组件大纲可排序；也可使用上移、下移按钮。</small>
  </div>
</template>
