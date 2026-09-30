<script setup lang="ts">
defineProps<{ page: number; pageSize: number; total: number; selectedCount: number; loading: boolean }>()
defineEmits<{ page: [value: number]; size: [value: number]; export: [] }>()
</script>

<template>
  <div class="inventory-pagination inventory-list-footer">
    <div class="inventory-list-selection">已选 {{ selectedCount }} 条
      <el-button :disabled="!selectedCount || loading" @click="$emit('export')">导出所选行 CSV</el-button>
    </div>
    <el-pagination :current-page="page" :page-size="pageSize" :total="total"
      :page-sizes="[20, 50, 100]" layout="total, sizes, prev, pager, next, jumper"
      @current-change="$emit('page', $event)" @size-change="$emit('size', $event)" />
  </div>
</template>
