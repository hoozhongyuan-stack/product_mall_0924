import { computed, onMounted, ref, type Ref } from 'vue'
import { api } from '../../api'
import type { PageConfig } from './types'
export function useEditorRuntime(editor: Ref<PageConfig | null>) {
  const runtimeSchema = ref<number | null>(null)
  const runtimeLoading = ref(true)
  const runtimeError = ref('')
  async function loadRuntime() {
    runtimeLoading.value = true
    runtimeError.value = ''
    try {
      const result = await api<{ runtimeSchemaVersion: number }>('/pages/capabilities')
      if (![1, 2, 3, 4].includes(result.runtimeSchemaVersion)) throw new Error('运行时信息不完整。')
      runtimeSchema.value = result.runtimeSchemaVersion
    } catch { runtimeSchema.value = null; runtimeError.value = '无法读取小程序兼容状态，请重试。' }
    finally { runtimeLoading.value = false }
  }
  onMounted(() => { void loadRuntime() })
  return { runtimeSchema, runtimeLoading, runtimeError, loadRuntime,
    runtimeBlocked: computed(() => !!editor.value && editor.value.schemaVersion > 1 && editor.value.schemaVersion > (runtimeSchema.value || 0)) }
}
