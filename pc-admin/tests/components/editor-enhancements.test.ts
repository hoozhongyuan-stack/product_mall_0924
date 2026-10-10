import { describe, expect, it } from 'vitest'
import { ref } from 'vue'
import { useEditorHistory, reorderComponent, duplicateComponent } from '../../src/views/pages/editor-history'
import { drawArea, moveArea, resizeArea } from '../../src/views/pages/hotzone-geometry'
import type { PageConfig } from '../../src/views/pages/types'

const config = (): PageConfig => ({ schemaVersion: 1, pageType: 'MICRO',
  theme: { pageBackgroundColor: '#ffffff', headerBackgroundColor: '#ffffff', brandTextColor: '#20252a' },
  components: ['one', 'two'].map((id, i) => ({ componentId: id, type: 'NOTICE', visible: true, sortOrder: i + 1, props: { text: id } })) })

describe('editor history and composition', () => {
  it('undoes and redoes independent content, and clears redo after a new edit', () => {
    const editor = ref<PageConfig | null>(config())
    const history = useEditorHistory(editor)
    history.reset()
    const original = editor.value!
    editor.value = { ...original, components: original.components.slice(0, 1) }
    expect(history.canUndo.value).toBe(true)
    history.undo()
    expect(editor.value?.components).toHaveLength(2)
    expect(editor.value).not.toBe(original)
    history.redo()
    expect(editor.value?.components).toHaveLength(1)
    history.undo()
    editor.value = { ...editor.value!, theme: { ...editor.value!.theme, pageBackgroundColor: '#fff2f0' } }
    expect(history.canRedo.value).toBe(false)
    history.reset()
    expect(history.canUndo.value).toBe(false)
    history.stop()
  })
  it('bounds history and preserves input when moving and copying components', () => {
    const original = config()
    const moved = reorderComponent(original, 'one', 'two')
    expect(moved.components.map(c => c.componentId)).toEqual(['two', 'one'])
    expect(original.components.map(c => c.componentId)).toEqual(['one', 'two'])
    const copy = duplicateComponent(original, 'one')
    expect(copy.components).toHaveLength(3)
    expect(copy.components[1].componentId).not.toBe('one')
    expect(copy.components[1].props).toEqual(original.components[0].props)
    expect(copy.components[1].props).not.toBe(original.components[0].props)
    expect(copy.components.map(c => c.sortOrder)).toEqual([1, 2, 3])
    expect(reorderComponent(original, 'missing', 'two')).toBe(original)
    const full = { ...original, components: Array.from({ length: 40 }, (_, i) => ({ ...original.components[0], componentId: String(i), sortOrder: i + 1 })) }
    expect(duplicateComponent(full, '0')).toBe(full)
    const editor = ref<PageConfig | null>(original)
    const history = useEditorHistory(editor, 2)
    history.reset()
    for (const color of ['#111111', '#222222', '#333333']) editor.value = { ...editor.value!, theme: { ...editor.value!.theme, brandTextColor: color } }
    history.undo(); history.undo(); history.undo()
    expect(editor.value?.theme.brandTextColor).toBe('#111111')
    history.stop()
  })
})

describe('hotzone geometry', () => {
  const area = { x: .2, y: .3, width: .4, height: .2, link: { type: 'FUNCTION' as const, targetId: 'CATALOG' } }
  it('draws in either direction and clamps normalized bounds', () => {
    expect(drawArea({ x: .9, y: .8 }, { x: -.1, y: 1.3 })).toEqual({ x: 0, y: .8, width: .9, height: .2 })
    expect(drawArea({ x: .1, y: .1 }, { x: .1, y: .1 }).width).toBe(0)
    const edge = drawArea({ x: .12345, y: .12345 }, { x: 1, y: 1 })
    expect(edge.x + edge.width).toBeLessThanOrEqual(1)
    expect(edge.y + edge.height).toBeLessThanOrEqual(1)
  })
  it('moves and resizes within the image while retaining the target', () => {
    expect(moveArea(area, 1, -1)).toEqual({ ...area, x: .6, y: 0 })
    expect(resizeArea(area, 2, 2)).toEqual({ ...area, width: .8, height: .7 })
    expect(resizeArea(area, -.9, -.9).width).toBeGreaterThan(0)
    const tiny = resizeArea({ ...area, x: .999, width: .001 }, 0, 0)
    expect(tiny.x + tiny.width).toBeLessThanOrEqual(1)
    expect(area.x).toBe(.2)
  })
})
