<script setup lang="ts">
withDefaults(defineProps<{count: number; unit?: string; loading?: boolean}>(), {unit: '条', loading: false})
defineEmits<{clear: []}>()
</script>
<template><div class="admin-selection-bar"><span role="status" aria-live="polite">已选 <strong>{{ count }}</strong> {{ unit }}</span><button v-if="count" class="text-button" :disabled="loading" @click="$emit('clear')">清空勾选</button><div class="admin-selection-actions"><slot /></div></div></template>
