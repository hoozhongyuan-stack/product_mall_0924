<script setup lang="ts">
import { ref, watch } from 'vue'
import type { HotzoneArea } from './types'
import { drawArea, moveArea, resizeArea, type Point } from './hotzone-geometry'

const props = defineProps<{ assetId: string; areas: HotzoneArea[]; disabled?: boolean }>()
const emit = defineEmits<{ change: [areas: HotzoneArea[]] }>()
const surface = ref<HTMLElement | null>(null)
const failed = ref(false)
const selected = ref(-1)
const drawing = ref<ReturnType<typeof drawArea> | null>(null)
const temporary = ref<HotzoneArea[] | null>(null)
let gesture: { pointer: number; start: Point; index: number; mode: 'draw' | 'move' | 'resize'; snapshot: string; areas: HotzoneArea[] } | null = null
watch(() => props.assetId, () => { failed.value = false; gesture = null; drawing.value = null; temporary.value = null })
function point(event: PointerEvent): Point {
  const bounds = surface.value!.getBoundingClientRect()
  return { x: (event.clientX - bounds.left) / bounds.width, y: (event.clientY - bounds.top) / bounds.height }
}
function start(event: PointerEvent, index = -1, mode: 'draw' | 'move' | 'resize' = 'draw') {
  if (props.disabled || failed.value || event.button !== 0 || (index < 0 && props.areas.length >= 20)) return
  const bounds = surface.value!.getBoundingClientRect()
  if (!bounds.width || !bounds.height) return
  event.preventDefault()
  selected.value = index
  surface.value!.setPointerCapture(event.pointerId)
  gesture = { pointer: event.pointerId, start: point(event), index, mode, snapshot: JSON.stringify([props.assetId, props.areas]), areas: props.areas }
}
function move(event: PointerEvent) {
  if (!gesture || gesture.pointer !== event.pointerId) return
  const current = point(event)
  const { start, areas, index, mode } = gesture
  if (mode === 'draw') { drawing.value = drawArea(start, current); return }
  const original = areas[index]!
  const adjusted = mode === 'move' ? moveArea(original, current.x - start.x, current.y - start.y) : resizeArea(original, current.x - start.x, current.y - start.y)
  temporary.value = areas.map((area, i) => i === index ? adjusted : area)
}
function finish(event: PointerEvent) {
  if (!gesture || gesture.pointer !== event.pointerId) return
  move(event)
  const valid = !props.disabled && gesture.snapshot === JSON.stringify([props.assetId, props.areas])
  if (valid && gesture.mode === 'draw' && drawing.value && drawing.value.width >= .01 && drawing.value.height >= .01) {
    emit('change', [...gesture.areas, { ...drawing.value, link: { type: 'FUNCTION', targetId: 'CATALOG' } }])
    selected.value = gesture.areas.length
  } else if (valid && temporary.value) emit('change', temporary.value)
  cancel()
}
function cancel() { gesture = null; drawing.value = null; temporary.value = null }
function style(area: { x: number; y: number; width: number; height: number }) {
  return { left: `${area.x * 100}%`, top: `${area.y * 100}%`, width: `${area.width * 100}%`, height: `${area.height * 100}%` }
}
function keyMove(event: KeyboardEvent, index: number) {
  if (props.disabled || !['ArrowLeft', 'ArrowRight', 'ArrowUp', 'ArrowDown'].includes(event.key)) return
  event.preventDefault()
  const step = event.shiftKey ? .05 : .01
  const dx = event.key === 'ArrowLeft' ? -step : event.key === 'ArrowRight' ? step : 0
  const dy = event.key === 'ArrowUp' ? -step : event.key === 'ArrowDown' ? step : 0
  emit('change', props.areas.map((area, i) => i === index ? moveArea(area, dx, dy) : area))
}
</script>

<template>
  <div v-if="assetId" class="hotzone-tool">
    <p class="help-text">在图片空白处拖动画框；拖动区域移动，拖动右下角缩放。方向键可移动，下面可精确填写坐标。</p>
    <p v-if="failed" role="alert">图片无法读取，请重新选择素材。</p>
    <div v-else ref="surface" class="hotzone-canvas" aria-label="热区绘制画布" @pointerdown="start($event)" @pointermove="move" @pointerup="finish" @pointercancel="cancel" @lostpointercapture="cancel">
      <img :src="`/api/v1/admin/assets/${encodeURIComponent(assetId)}/file`" alt="用于绘制点击区域的图片" draggable="false" @error="failed = true">
      <div v-for="(area, index) in temporary || areas" :key="index" class="hotzone-frame" :class="{ selected: selected === index }" :style="style(area)" role="button" :tabindex="disabled ? -1 : 0" :aria-label="`点击区域 ${index + 1}，方向键移动`" @pointerdown.stop="start($event, index, 'move')" @keydown="keyMove($event, index)" @focus="selected = index">
        <span>{{ index + 1 }}</span><button type="button" :disabled="disabled" class="hotzone-resize" :aria-label="`缩放点击区域 ${index + 1}，也可使用下方尺寸输入`" @pointerdown.stop="start($event, index, 'resize')" />
      </div>
      <div v-if="drawing" class="hotzone-frame drawing" :style="style(drawing)" />
    </div>
    <span role="status" class="help-text">已配置 {{ areas.length }} / 20 个区域</span>
  </div>
</template>

<style scoped>
.hotzone-tool{display:grid;gap:8px}.hotzone-tool p{margin:0}.hotzone-canvas{position:relative;touch-action:none;cursor:crosshair;user-select:none}.hotzone-canvas>img{display:block;width:100%;height:auto;pointer-events:none}.hotzone-frame{position:absolute;outline:2px solid var(--mall-color-brand);background:#a3202b19;cursor:move;min-width:3px;min-height:3px}.hotzone-frame:focus-visible,.hotzone-frame.selected{outline-width:3px;outline-offset:1px}.hotzone-frame>span{display:inline-block;background:var(--mall-color-brand);color:#fff;padding:2px 6px;font-size:12px}.hotzone-resize{position:absolute;right:-4px;bottom:-4px;width:16px!important;min-height:16px!important;height:16px;padding:0!important;border:2px solid #fff;border-radius:2px;background:var(--mall-color-brand);cursor:nwse-resize}.drawing{pointer-events:none}
</style>
