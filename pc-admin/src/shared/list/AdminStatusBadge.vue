<script setup lang="ts">
import { computed } from 'vue'
import { statusTone, type StatusTone } from './list-state.mjs'
const props = defineProps<{status: string; label?: string; tone?: StatusTone}>()
const semanticTone = computed(() => props.tone ?? statusTone(props.status))
</script>
<template><span class="admin-status-badge" :class="`admin-status-${semanticTone}`">{{ label ?? status }}</span></template>
