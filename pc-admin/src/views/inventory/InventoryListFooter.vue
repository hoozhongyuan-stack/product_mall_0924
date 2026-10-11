<script setup lang="ts">
import AdminListPager from '../../shared/list/AdminListPager.vue'
import AdminSelectionBar from '../../shared/list/AdminSelectionBar.vue'
defineProps<{ page: number; pageSize: number; total: number; selectedCount: number; loading: boolean }>()
defineEmits<{ clear: []; page: [value: number]; size: [value: number]; export: [] }>()
</script>

<template>
  <div class="inventory-pagination inventory-list-footer">
    <AdminSelectionBar :count="selectedCount" :loading="loading" @clear="$emit('clear')"><el-button :disabled="!selectedCount || loading" @click="$emit('export')">导出所选行 CSV</el-button></AdminSelectionBar>
    <AdminListPager :page="page" :page-size="pageSize" :total="total" :loading="loading" @change="(value,size)=>{ if(size !== pageSize) $emit('size',size); else $emit('page',value) }"/>
  </div>
</template>
