import { computed, ref, watch, type Ref } from 'vue'
import { normalizeConfig, type PageConfig } from './types'

export function useEditorHistory(editor: Ref<PageConfig | null>, limit = 50) {
  const past = ref<string[]>([])
  const future = ref<string[]>([])
  let current = ''
  let applying = false
  const stop = watch(editor, value => {
    const next = value ? JSON.stringify(value) : ''
    if (!applying && current && next !== current) {
      past.value = [...past.value, current].slice(-limit)
      future.value = []
    }
    current = next
  }, { deep: true, flush: 'sync' })
  function reset() { past.value = []; future.value = []; current = editor.value ? JSON.stringify(editor.value) : '' }
  function restore(snapshot: string) {
    applying = true
    editor.value = JSON.parse(snapshot) as PageConfig
    current = snapshot
    applying = false
  }
  function undo() {
    const previous = past.value.at(-1)
    if (!previous || !current) return
    future.value = [...future.value, current]
    past.value = past.value.slice(0, -1)
    restore(previous)
  }
  function redo() {
    const next = future.value.at(-1)
    if (!next || !current) return
    past.value = [...past.value, current].slice(-limit)
    future.value = future.value.slice(0, -1)
    restore(next)
  }
  return { canUndo: computed(() => past.value.length > 0), canRedo: computed(() => future.value.length > 0), reset, undo, redo, stop }
}

export function reorderComponent(config: PageConfig, id: string, target: string): PageConfig {
  const from = config.components.findIndex(item => item.componentId === id)
  const to = config.components.findIndex(item => item.componentId === target)
  if (from < 0 || to < 0 || from === to) return config
  const item = config.components[from]!
  const remaining = config.components.filter(component => component.componentId !== id)
  return normalizeConfig({ ...config, components: [...remaining.slice(0, to), item, ...remaining.slice(to)] })
}

export function duplicateComponent(config: PageConfig, id: string): PageConfig {
  const index = config.components.findIndex(item => item.componentId === id)
  if (index < 0 || config.components.length >= 40) return config
  const item = JSON.parse(JSON.stringify(config.components[index]))
  const copy = { ...item, componentId: crypto.randomUUID() }
  return normalizeConfig({ ...config, components: [...config.components.slice(0, index + 1), copy, ...config.components.slice(index + 1)] })
}
