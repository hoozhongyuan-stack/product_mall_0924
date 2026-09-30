<script setup lang="ts">
const model = defineModel<string[] | null>({ required: true })
function daysRange(days: number) {
  const end = new Date()
  const start = new Date(end)
  start.setDate(start.getDate() - days + 1)
  return [start, end]
}
const shortcuts = [
  { text: '今天', value: () => daysRange(1) },
  { text: '近 7 天', value: () => daysRange(7) },
  { text: '近 30 天', value: () => daysRange(30) },
  { text: '本月', value: () => { const end = new Date(); return [new Date(end.getFullYear(), end.getMonth(), 1), end] } },
]
</script>

<template>
  <el-date-picker v-model="model" class="inventory-date-range" type="daterange" value-format="YYYY-MM-DD"
    format="YYYY-MM-DD" range-separator="至" start-placeholder="开始日期" end-placeholder="结束日期"
    popper-class="inventory-date-popover" :shortcuts="shortcuts" unlink-panels clearable />
</template>
