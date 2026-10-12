<script setup lang="ts">
withDefaults(defineProps<{page: number; pageSize: number; hasPrevious: boolean; hasNext: boolean; loading?: boolean}>(), {loading:false})
defineEmits<{previous: []; next: []; size: [value:number]}>()
</script>
<template><nav class="admin-list-pager admin-cursor-pager" aria-label="记录分页" :aria-busy="loading"><span v-if="loading" role="status">正在读取分页信息…</span><template v-else><span>第 {{page}} 页</span><label>每页条数<select :value="pageSize" :disabled="loading" @change="$emit('size',Number(($event.target as HTMLSelectElement).value))"><option v-for="size in [20,50,100]" :key="size" :value="size">{{size}} 条 / 页</option></select></label><button class="secondary-button" :disabled="loading || !hasPrevious" @click="$emit('previous')">上一页</button><button class="secondary-button" :disabled="loading || !hasNext" @click="$emit('next')">下一页</button><small>按记录顺序翻页</small></template></nav></template>
