<script setup lang="ts">
import { ElConfigProvider, ElPagination } from 'element-plus'
import zhCn from 'element-plus/es/locale/lang/zh-cn'
withDefaults(defineProps<{page: number; pageSize: number; total: number; loading?: boolean}>(), {loading: false})
defineEmits<{change: [page: number, size: number]}>()
</script>
<template>
  <nav class="admin-list-pager" aria-label="列表分页">
    <ElConfigProvider :locale="zhCn"><ElPagination :current-page="page" :page-size="pageSize" :total="total" :disabled="loading"
      :page-sizes="[20, 50, 100]" layout="total, sizes, prev, pager, next, jumper"
      @current-change="$emit('change', $event, pageSize)" @size-change="$emit('change', 1, $event)" /></ElConfigProvider>
  </nav>
</template>
