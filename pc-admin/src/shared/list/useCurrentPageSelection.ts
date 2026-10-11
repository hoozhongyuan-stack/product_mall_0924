import { computed, ref, watch } from 'vue'
import { toggleSelection } from './list-state.mjs'
export function useCurrentPageSelection<T>(rows: () => readonly T[], key: (row: T) => string) {
  const selectedIds = ref<string[]>([])
  const allowedIds = computed(() => rows().map(key))
  const selectedRows = computed(() => rows().filter(row => selectedIds.value.includes(key(row))))
  const allSelected = computed(() => allowedIds.value.length > 0 && selectedRows.value.length === allowedIds.value.length)
  const indeterminate = computed(() => selectedRows.value.length > 0 && !allSelected.value)
  const clear = () => { selectedIds.value = [] }
  watch(rows, clear)
  return { selectedIds, selectedRows, allSelected, indeterminate, clear,
    toggle: (id: string, checked: boolean) => { selectedIds.value = toggleSelection(selectedIds.value, id, checked, allowedIds.value) },
    togglePage: (checked: boolean) => { selectedIds.value = checked ? [...allowedIds.value] : [] },
  }
}
