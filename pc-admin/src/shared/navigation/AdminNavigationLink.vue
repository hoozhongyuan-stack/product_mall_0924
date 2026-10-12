<script setup lang="ts">
import { useAttrs } from 'vue'
import { RouterLink } from 'vue-router'
defineOptions({ inheritAttrs: false })
const props = defineProps<{ to: string; navigate: (target: string) => Promise<void> | void }>()
const attrs = useAttrs()
function click(event: MouseEvent) {
  if (event.defaultPrevented || event.button !== 0 || event.metaKey || event.ctrlKey || event.shiftKey || event.altKey || String(attrs.target || '').toLowerCase() === '_blank') return
  event.preventDefault()
  void props.navigate(props.to)
}
</script>
<template><RouterLink :to="to" custom v-slot="{ href }"><a v-bind="attrs" :href="href" @click="click"><slot /></a></RouterLink></template>
