<script setup lang="ts">
import { ref } from 'vue'
import { ElDialog } from 'element-plus'
const props = defineProps<{modelValue: string[]; columns: {id: string; label: string; required?: boolean}[]}>()
const emit = defineEmits<{'update:modelValue': [hidden: string[]]}>()
const open = ref(false)
function toggle(id: string, visible: boolean) {
  if (props.columns.find(column => column.id === id)?.required) return
  emit('update:modelValue', visible ? props.modelValue.filter(value => value !== id) : [...new Set([...props.modelValue, id])])
}
</script>
<template><button class="secondary-button" type="button" @click="open=true">列设置</button><ElDialog v-if="open" v-model="open" title="设置列表显示项" width="460px" :close-on-click-modal="false"><p>主信息、勾选和操作列固定显示。</p><label v-for="column in columns" :key="column.id" class="check-row"><input type="checkbox" :checked="!modelValue.includes(column.id)" :disabled="column.required" @change="toggle(column.id,($event.target as HTMLInputElement).checked)">{{ column.label }}</label><template #footer><button class="secondary-button" @click="emit('update:modelValue',[])">恢复默认</button><button class="primary-button" @click="open=false">完成</button></template></ElDialog></template>
